"""CR of CL37: recompute the sampled part bars from docs/CL37-sessions.jsonl without scripts/usage.py.

Also: how sensitive the -26% / -31% are to the cut and to the role labels.
Run: python3 -I scripts/review/CR-cl37-usage-ledger-tokens-tools-agents-per-ses-recompute.py [rows.jsonl]
"""
import json, statistics, sys
from collections import Counter
from datetime import datetime

path = sys.argv[1] if len(sys.argv) > 1 else "docs/CL37-sessions.jsonl"
rows = [json.loads(l) for l in open(path) if l.strip()]
B = datetime.fromisoformat("2026-11-23T00:27:22+00:00")
A = datetime.fromisoformat("2026-11-23T01:25:19+00:00")
t = lambda r: datetime.fromisoformat(r["started"])

def side(rs, pred):
    return [r for r in rs if pred(t(r))]

def stats(rs, label):
    cost = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
    w = [r for r in rs if (r.get("work") or {}).get("commits")]
    per100 = [r["cost_usd"] / ((r["work"]["lines_added"] + r["work"]["lines_deleted"]) / 100)
              for r in w if r["work"]["lines_added"] + r["work"]["lines_deleted"] > 0]
    commits = [(r.get("work") or {}).get("commits", 0) for r in rs]
    m = lambda v: round(statistics.median(v), 2) if v else None
    print(f"  {label}: n={len(cost)} median cost ${m(cost)}  mean ${round(statistics.mean(cost),2) if cost else None}"
          f"  $/100 lines {m(per100)} (n {len(per100)})  commits {m(commits)}  zero-commit sessions {sum(1 for c in commits if not c)}")
    return m(cost), m(per100)

parts = [r for r in rows if r.get("role") == "part"]
print("roles:", dict(Counter(r.get("role") for r in rows)))
print("part, the step's cut:")
b = stats(side(parts, lambda x: x <= B), "before"); a = stats(side(parts, lambda x: x >= A), "after")
print(f"  change cost {round((a[0]/b[0]-1)*100)}%  $/100 lines {round((a[1]/b[1]-1)*100)}%")

print("part, before limited to the last 4 days (2026-11-19..cut), same window length order as after:")
stats(side(parts, lambda x: datetime.fromisoformat("2026-11-19T00:00:00+00:00") <= x <= B), "before-4d")

print("part, after split by part prefix (CL vs other):")
after = side(parts, lambda x: x >= A)
print("  after prefixes:", dict(Counter((r.get("part") or "?").rstrip("0123456789") for r in after)))
print("  before prefixes:", dict(Counter((r.get("part") or "?").rstrip("0123456789") for r in side(parts, lambda x: x <= B)).most_common(12)))

print("orchestrator ($/commit):")
orc = [r for r in rows if r.get("role") == "orchestrator"]
for lbl, rs in (("before", side(orc, lambda x: x <= B)), ("after", side(orc, lambda x: x >= A))):
    v = [r["cost_usd"] / r["work"]["commits"] for r in rs if (r.get("work") or {}).get("commits")]
    print(f"  {lbl}: n={len(rs)} with commits {len(v)} median $/commit {round(statistics.median(v),2) if v else None}")

print("review sessions after the cut:", len(side([r for r in rows if r.get("role") == "review"], lambda x: x >= A)))
print("rows with source != ccr (hook-recorded):", sum(1 for r in rows if r.get("source") != "ccr"))
