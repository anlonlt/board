#!/usr/bin/env python3
"""Mutations of scripts/kit.py: each must turn scripts/test_kit.py red, for its own case (BD9).

    python3 -I scripts/kit-mutations.py
"""
import os, shutil, subprocess, sys, tempfile
M = [  # (what is broken, old, new)
 ("diff ignores field values", "if a[i].get(k, KeyError) != b[i].get(k, KeyError):", "if False:"),
 ("diff misses comments only in the dump", 'out.append(("missing", coll, i, None))', "pass"),
 ("diff exits 0 always", "return 1 if ds else 0", "return 0"),
 ("no race tagging", 'kind + (" (race)" if race else "")', "kind"),
 ("sync drops comments not re-read", "if full:\n        for i in old_c:", "if True:\n        for i in old_c:"),
 ("sync keeps posts not listed", "            os.remove(os.path.join(pdir, i + \".json\"))", "            pass"),
 ("cursor not saved", 'st["cursor"] = at', "pass"),
 ("no overlap", "iso(st[\"cursor\"]) - OVERLAP", "iso(st[\"cursor\"])"),
 ("sync accepts no posts", "    if not posts:\n        raise SystemExit", "    if False:\n        raise SystemExit"),
 ("post cut by default", "lines.append(cut(d.get(\"body\"), head))", "lines.append(cut(d.get(\"body\"), head or 1500))"),
 ("post --head ignored", "return s if n is None or len(s) <= n", "return s if True or len(s) <= n"),
 ("post without reply_to", 'to = (" → " + c["reply_to"]) if c.get("reply_to") else ""', 'to = ""'),
 ("open counts any answer", "or (o[\"post_id\"] == h[\"post_id\"] and ad.ts(o[\"created_at\"]) >= t0) for o in answers):", "or True for o in answers):"),
 ("open ignores reply_to", '(o.get("reply_to") and o["reply_to"] in (h["doc_id"], h.get("thread_id")))', "False"),
 ("since ignores done_at", 'for s in ("created_at", "updated_at", "done_at"):', 'for s in ("created_at", "updated_at"):'),
 ("since uses created_at only", "s = max((c.get(k) or \"\") for k in STAMPS)", "s = c.get(\"created_at\") or \"\""),
 ("config ignores the owner's name", 'return _Waits(f.get("owner", "owner"), f.get("clerk", "clerk"))', 'return _Waits()'),
 ("config ignores the stamps", 'STAMPS = tuple([f.get("created", "created_at")] + list(f.get("later_stamps", STAMPS[1:])))', 'pass'),
 ("config ignores the overlap", 'OVERLAP = timedelta(hours=mi.get("overlap_hours", 1))', 'pass'),
 ("session wrong field", 'json.load(fh).get("session_id")', 'json.load(fh).get("part")'),
]
src = open("scripts/kit.py").read()
bad = 0
for name, old, new in M:
    assert src.count(old) == 1, name
    d = os.path.join(tempfile.mkdtemp(), "scripts"); os.makedirs(d)
    shutil.copy("scripts/test_kit.py", d)
    open(os.path.join(d, "kit.py"), "w").write(src.replace(old, new))
    r = subprocess.run([sys.executable, "-I", os.path.join(d, "test_kit.py")], capture_output=True, text=True)
    red = [l[5:] for l in r.stdout.splitlines() if l.startswith("FAIL")] or ([r.stderr.strip().splitlines()[-1]] if r.returncode else [])
    bad += not red
    print(("RED   " if red else "GREEN ") + name + " -> " + "; ".join(red))
    shutil.rmtree(os.path.dirname(d))
print("mutations not caught:", bad)
