#!/usr/bin/env python3
"""scripts/kit.py on small boards built here (part BD9). Each case was seen red first by breaking the code it
guards (the mutations are listed in post BD9-kit-mirror).

    python3 -I scripts/test_kit.py
"""
import importlib.util, io, json, os, shutil, sys, tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kit", os.path.join(HERE, "kit.py"))
kit = importlib.util.module_from_spec(spec); spec.loader.exec_module(kit)
fails = 0


def case(name, ok, detail=""):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "\n     " + str(detail)))


def board(d, posts, comments):
    for coll, docs in (("posts", posts), ("comments", comments)):
        os.makedirs(os.path.join(d, coll), exist_ok=True)
        for i, v in docs.items():
            with open(os.path.join(d, coll, i + ".json"), "w") as fh:
                json.dump(v, fh, indent=2)
    return d


P = {"A-one": {"title": "A one", "column": "info", "part": "A", "session_id": "session_A", "created_at": "2026-11-19T10:00:00Z",
               "body": "x" * 3000 + "THE END"},
     "B-two": {"title": "B two", "column": "done", "part": "B", "session_id": "session_B", "created_at": "2026-11-18T10:00:00Z",
               "body": "short", "done_at": "2026-11-19T11:00:00Z"}}
C = {"his1": {"post_id": "A-one", "from": "owner", "created_at": "2026-11-19T10:05:00.100Z", "text": "Why is this red?",
              "read": True, "read_at": "2026-11-19T10:06:00Z"},
     "ans1": {"post_id": "A-one", "from": "A", "created_at": "2026-11-19T10:07:00Z", "text": "Because.", "reply_to": "his1"},
     "his2": {"post_id": "B-two", "from": "owner", "created_at": "2026-11-19T12:00:00.000Z", "text": "And this one?", "read": False},
     "like": {"post_id": "B-two", "from": "owner", "created_at": "2026-11-19T12:01:00Z", "like": True, "comment_id": "x"},
     "his3": {"post_id": "A-one", "from": "owner", "created_at": "2026-11-19T12:30:00Z", "text": "Late?"},
     "ans3": {"post_id": "A-one", "from": "A", "created_at": "2026-11-19T12:26:00Z", "text": "Typed early.", "reply_to": "his3"},
     "old":  {"post_id": "B-two", "from": "B", "created_at": "2026-11-18T10:30:00Z", "text": "An old line."}}

tmp = tempfile.mkdtemp()
try:
    m = os.path.join(tmp, "mirror")
    full = board(os.path.join(tmp, "full"), P, C)
    kit.sync(m, full, "2026-11-19T13:00:00Z", full=True)

    # 1. a bootstrap from a full dump equals that dump: zero differences
    case("bootstrap copy == its dump", kit.diff(m, full) == [], kit.diff(m, full))

    # 2. THE PLANT: one field changed on an OLD comment in the dump file must be named, field included
    plant = os.path.join(tmp, "plant"); shutil.copytree(full, plant)
    with open(os.path.join(plant, "comments", "old.json")) as fh:
        d = json.load(fh)
    d["text"] = "An old line, edited."
    with open(os.path.join(plant, "comments", "old.json"), "w") as fh:
        json.dump(d, fh)
    case("plant on an old comment caught, with its field", kit.diff(m, plant) == [("changed", "comments", "old", "text")], kit.diff(m, plant))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = kit.main(["--mirror", m, "diff", plant])
    case("diff exits 1 on a difference", rc == 1 and "1 differences" in out.getvalue(), (rc, out.getvalue()))

    # 3. a missing field, a removed comment and a new one are all named
    gone = os.path.join(tmp, "gone"); shutil.copytree(full, gone)
    os.remove(os.path.join(gone, "comments", "like.json"))
    with open(os.path.join(gone, "comments", "new.json"), "w") as fh:
        json.dump({"post_id": "A-one", "from": "owner", "created_at": "2026-11-19T13:30:00Z", "text": "new"}, fh)
    with open(os.path.join(gone, "comments", "his2.json")) as fh:
        d = json.load(fh)
    del d["read"]
    with open(os.path.join(gone, "comments", "his2.json"), "w") as fh:
        json.dump(d, fh)
    ds = kit.diff(m, gone)
    case("removed, new and a dropped field all named",
         set(ds) == {("extra", "comments", "like", None), ("missing", "comments", "new", None), ("changed", "comments", "his2", "read")}, ds)
    ds = kit.diff(m, gone, ("2026-11-19T13:20:00Z", "2026-11-19T13:40:00Z"))
    case("a document stamped inside the read window is a race, the others are not",
         ("missing (race)", "comments", "new", None) in ds and ("extra", "comments", "like", None) in ds, ds)

    # 4. an incremental sync: overlays comments it read, keeps the others, replaces posts in full
    inc = board(os.path.join(tmp, "inc"), {"A-one": dict(P["A-one"], column="done")},
                {"his2": dict(C["his2"], read=True, read_at="2026-11-19T13:10:00Z"),
                 "n2": {"post_id": "A-one", "from": "owner", "created_at": "2026-11-19T13:20:00Z", "text": "Next?"}})
    r = kit.sync(m, inc, "2026-11-19T13:40:00Z")
    after = kit.load_dir(m + "/board", "comments")
    case("incremental: 1 new, 1 changed, nothing removed; old comments kept", r[2:] == (1, 1, 0) and "old" in after and after["his2"]["read"] is True, (r, sorted(after)))
    case("posts replaced in full: a post not listed is removed", sorted(kit.load_dir(m + "/board", "posts")) == ["A-one"] and r[1] == 1, r)
    case("the cursor is the --at time", kit.read_state(m)["cursor"] == "2026-11-19T13:40:00Z")
    case("reads: the overlap starts one hour before the cursor, on every stamp",
         all('"%s", ">=", "2026-11-19T12:40:00Z"' % s in "\n".join(kit.reads(m)) for s in kit.STAMPS), kit.reads(m))
    try:
        kit.sync(m, board(os.path.join(tmp, "noposts"), {}, {}), "2026-11-19T14:00:00Z")
        case("a sync without the posts list is refused", False, "accepted")
    except SystemExit:
        case("a sync without the posts list is refused", True)

    # 5. queries
    kit.sync(m, full, "2026-11-19T13:00:00Z", full=True)
    out = kit.post(m, "A-one")
    case("post: the whole body by default", "THE END" in out and "x" * 3000 in out, out[-200:])
    case("post: its comments, with reply_to", "his1" in out and "ans1 → his1" in out, out[-300:])
    out = kit.post(m, "A-one", head=100)
    case("post --head cuts the body", "THE END" not in out and "more chars" in out, out[:300])
    out = kit.open_(m)
    case("open: his unanswered comment, not his answered one nor his Like",
         "his2 on B-two" in out and "his1" not in out and "like" not in out, out)
    case("open: an answer by reply_to counts even when stamped before his comment", "his3" not in out, out)
    case("since: a comment read after the time shows (read_at, not only created_at)", "his1" in kit.since(m, "2026-11-19T10:05:30Z"))
    out = kit.since(m, "2026-11-19T11:30:00Z")
    case("since: what changed after the time, nothing before", "his2" in out and "like" in out and "old" not in out and "his1 " not in out, out)
    case("since: a post moved to done after the time shows", "post B-two" in kit.since(m, "2026-11-19T10:30:00Z"))
    case("session: the post's session id", kit.session(m, "B-two") == "session_B", kit.session(m, "B-two"))

    # 6. the config renames: another owner name and other stamps (the kit's own job, anlonlt/board)
    cfg = os.path.join(tmp, "cfg.json")
    with open(cfg, "w") as fh:
        json.dump({"fields": {"owner": "boss", "clerk": "clerk", "created": "created_at", "later_stamps": ["seen_at"]},
                   "mirror": {"overlap_hours": 2}}, fh)
    kit.configure(cfg)
    case("config: the stamps a sync reads by", kit.STAMPS == ("created_at", "seen_at"), kit.STAMPS)
    case("config: the overlap", "2026-11-19T11:00:00Z" in "\n".join(kit.reads(m)), kit.reads(m))
    case("config: open follows the owner's name (none of 'owner' counts now)", kit.open_(m).startswith("nothing"), kit.open_(m))

    # 7. sync-db (direct SQLite full + O(delta) sync via changes table)
    import sqlite3
    db_file = os.path.join(tmp, "board.db")
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE docs (collection TEXT, doc_id TEXT, version INTEGER, data TEXT, updated_at TEXT, PRIMARY KEY (collection, doc_id))")
    conn.execute("CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT, doc_id TEXT, op TEXT, version INTEGER, at TEXT)")
    conn.execute("INSERT INTO docs VALUES ('posts', 'DB-1', 1, ?, '2026-11-20T00:00:00Z')", (json.dumps({"title": "One", "column": "info", "created_at": "2026-11-20T00:00:00Z"}),))
    conn.execute("INSERT INTO changes(collection, doc_id, op, version, at) VALUES ('posts', 'DB-1', 'set', 1, '2026-11-20T00:00:00Z')")
    conn.commit()
    m_db = os.path.join(tmp, "mirror_db")
    r_db1 = kit.sync_db(m_db, db_path=db_file, at="2026-11-20T00:01:00Z")
    conn.execute("INSERT INTO docs VALUES ('comments', 'c-db1', 1, ?, '2026-11-20T00:02:00Z')", (json.dumps({"post_id": "DB-1", "from": "boss", "text": "delta comment", "created_at": "2026-11-20T00:02:00Z"}),))
    conn.execute("INSERT INTO changes(collection, doc_id, op, version, at) VALUES ('comments', 'c-db1', 'set', 1, '2026-11-20T00:02:00Z')")
    conn.commit()
    r_db2 = kit.sync_db(m_db, db_path=db_file, at="2026-11-20T00:03:00Z")
    conn.close()
    case("sync-db: initial bootstrap + incremental delta sync from SQLite changes table",
         r_db1 == (1, 0, 0, 0, 0) and r_db2 == (0, 0, 1, 0, 0) and "c-db1" in kit.load_dir(m_db + "/board", "comments"),
         (r_db1, r_db2))
finally:
    shutil.rmtree(tmp)

print("%d failed" % fails)
sys.exit(1 if fails else 0)
