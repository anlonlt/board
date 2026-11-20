#!/usr/bin/env python3
"""Would WF1's one-way sync (comments where created_at >= last sync) give a git copy equal to the board?

Review CR-WF1-workflow-inventory (2026-11-19). Run on a full export (ArtifactData `list` posts and comments with
out_dir):  python3 -I scripts/mirror_holes.py EXPORT_DIR [--config config/<project>.json]

Kit version (anlonlt/board, BD9): field names and roles come from the config (`fields`); origin anlonlt/harmonize
scripts/review/WF1-workflow-inventory-mirror-holes.py (review CR-WF1).

It gave, on the board of 2026-11-19 23:50 UTC (173 posts, 1,279 comments):
  1 of 414 session replies is stamped before the comment it answers (240 s); 73 of 830 session comments are
  stamped on a whole minute; 18 carry the page's arrived_at correction. All 449 of his comments carry fields
  written after creation (read, read_at, delivered); 12 of 173 posts have an updated_at, 107 a done_at.
  Post bodies: median 6,098 chars, largest 35,165 (B13); a 1,500-char cut shows 37 of 169 whole, 25 % of the
  median. Reviews: 96 kind=review, 68 from the consultant (median 4,389 chars).
"""
import glob, json, os, statistics, sys
from datetime import datetime


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


args = sys.argv[1:]
F = {"author": "from", "owner": "owner", "reviewer": "consultant", "post": "post_id", "reply_to": "reply_to",
     "created": "created_at", "later_flags": ["read", "read_at", "delivered"], "post_updated": "updated_at",
     "post_moved": "done_at", "review_kind": ["kind", "review"], "arrived": "arrived_at"}
if "--config" in args:
    i = args.index("--config")
    F.update(json.load(open(args[i + 1], encoding="utf-8")).get("fields", {}))
    del args[i:i + 2]
d = args[0]
A, OWN, CR = F["author"], F["owner"], F["created"]
C = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(d + "/comments/*.json")}
P = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(d + "/posts/*.json")}
print("posts %d, comments %d" % (len(P), len(C)))
# 1. a reply stamped before the comment it answers: the stamp is typed by the session, so it is no safe cursor
bad = []
for i, c in C.items():
    p = C.get(c.get(F["reply_to"]) or "")
    if p and c.get(A) != OWN:
        dt = (ts(p[CR]) - ts(c[CR])).total_seconds()
        if dt > 0:
            bad.append((dt, i))
ses = [c for c in C.values() if c.get(A) != OWN]
print("session comments stamped BEFORE the comment they reply to: %d of %d replies (worst %.0f s)" % (
    len(bad), sum(1 for c in ses if c.get(F["reply_to"]) in C), max(bad)[0] if bad else 0))
print("session comments stamped on a whole minute (:00): %d of %d; with the page's arrived_at correction: %d" % (
    sum(c[CR][17:19] == "00" for c in ses), len(ses), sum(F["arrived"] in c for c in C.values())))
# 2. fields written after creation, which a created_at cursor never fetches again
own = [c for c in C.values() if c.get(A) == OWN]
print("his comments carrying fields written later (%s): %d of %d" % (" / ".join(F["later_flags"]),
    sum(any(k in c for k in F["later_flags"]) for c in own), len(own)))
print("posts with an updated_at: %d of %d; posts he moved (done_at): %d" % (
    sum(F["post_updated"] in p for p in P.values()), len(P), sum(F["post_moved"] in p for p in P.values())))
# 3. what a body cut at 1.5k leaves out
b = sorted(len(p.get("body") or "") for i, p in P.items() if not i.startswith("_"))
print("post bodies: median %d chars, largest %d; a 1,500-char cut shows the whole body of %d of %d posts, and %.0f%% of the median one" % (
    statistics.median(b), b[-1], sum(x <= 1500 for x in b), len(b), 150000 / statistics.median(b)))
rk, rv = F["review_kind"]
rev = [len(c.get("text") or "") for c in C.values() if c.get(A) == F["reviewer"]]
print("comments kind=review: %d; from the consultant: %d (median %d chars)" % (
    sum(c.get(rk) == rv for c in C.values()), len(rev), statistics.median(rev) if rev else 0))
