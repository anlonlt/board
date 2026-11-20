#!/usr/bin/env python3
"""kit_import.py on a planted state: memory files, awaits, preferences, posts; a rerun keeps what exists."""
import json, os, shutil, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0
def check(n, ok):
    global fails; print(("ok   " if ok else "FAIL ") + n); fails += not ok
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, ".kit")); shutil.copy(os.path.join(HERE, "kit_import.py"), os.path.join(d, ".kit"))
    st = {"preferences": ["short steps"], "projects": [{"id": "Weekly Bulletin", "title": "Weekly bulletin", "next": "draft 24 Nov",
          "where": ["every Friday"], "ids": {"page": "News"}, "waiting": [{"text": "new layout?", "default": "Keep", "options": ["New", "Keep"]}]}]}
    sf = os.path.join(d, "s.json"); json.dump(st, open(sf, "w"))
    run = lambda: subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "kit_import.py"), sf, "--out", os.path.join(d, "o"),
                                  "--now", "2026-11-20T00:00:00Z", "--session", "S1"], capture_output=True, text=True)
    r = run()
    s = open(os.path.join(d, ".story", "state", "weekly-bulletin.md")).read()
    check("state file: Next line second, ids kept", s.splitlines()[1] == "Next: draft 24 Nov" and "page: News" in s)
    check("awaits: the waiting item with its default", "- [weekly-bulletin] new layout? (default: Keep)" in open(os.path.join(d, ".story", "awaits.md")).read())
    check("preferences written", "short steps" in open(os.path.join(d, ".story", "preferences.md")).read())
    q = json.load(open(os.path.join(d, "o", "posts", "Q-weekly-bulletin-1.json")))
    check("question post with one-tap options and the default", q["column"] == "question" and q["options"] == ["New", "Keep"] and q["default_option"] == "Keep")
    w = json.loads(r.stdout.strip().splitlines()[-1])
    check("batch writes printed, 2 posts", len(w) == 2 and w[0]["doc_id"] == "P-weekly-bulletin")
    open(os.path.join(d, ".story", "state", "weekly-bulletin.md"), "a").write("my edit\n")
    r = run()
    check("a rerun keeps an edited state and adds no duplicate await", "KEPT" in r.stdout and "my edit" in open(os.path.join(d, ".story", "state", "weekly-bulletin.md")).read()
          and open(os.path.join(d, ".story", "awaits.md")).read().count("new layout?") == 1)

    # issue #2 (a real chat export): ids as a list of objects, chats as objects, a default outside the options,
    # the state handed over as the artifact's HTML, a French default ending with a period
    st2 = {"projects": [{"id": "flux", "title": "Flux", "next": "tester", "ids": [{"name": "liste", "value": "Demandes"}, "Site RH"],
           "chats": [{"date": "2026-11-18", "title": "Flux RH"}],
           "waiting": [{"text": "Publier ?", "default": "On attend lundi.", "options": ["Oui", "Non"]}]}]}
    hf = os.path.join(d, "a.html")
    open(hf, "w").write('<!doctype html><pre id="kit-state">' + json.dumps(st2).replace('"', "&quot;") + "</pre>")
    r = subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "kit_import.py"), hf, "--out", os.path.join(d, "o2"),
                        "--now", "2026-11-20T00:00:00Z"], capture_output=True, text=True)
    check("#2: the artifact's HTML is read, ids list and chat objects do not crash", r.returncode == 0)
    s2 = open(os.path.join(d, ".story", "state", "flux.md")).read() if r.returncode == 0 else ""
    check("#2: ids and chats written as text", "liste: Demandes" in s2 and "\n- Site RH\n" in s2 and "2026-11-18 · Flux RH" in s2)
    q2 = json.load(open(os.path.join(d, "o2", "posts", "Q-flux-1.json"))) if r.returncode == 0 else {}
    check("#2: a default outside the options marks no option", q2.get("options") == ["Oui", "Non"] and "default_option" not in q2)
    check("#2: no English added, no double period", q2.get("body") == "Publier ?\n\n→ On attend lundi")

    # issue #2, the survey's needs: what he approved once, what must never be touched, what the export could not read
    st3 = {"approved": ["editer la liste test"], "never": ["envoyer de vrais courriels"], "unread": ["avant le 14 octobre"],
           "projects": [{"id": "p3", "title": "P3", "next": "x"}]}
    f3 = os.path.join(d, "s3.json"); json.dump(st3, open(f3, "w"))
    os.remove(os.path.join(d, ".story", "preferences.md"))
    subprocess.run([sys.executable, "-I", os.path.join(d, ".kit", "kit_import.py"), f3, "--out", os.path.join(d, "o3")], capture_output=True)
    pf = open(os.path.join(d, ".story", "preferences.md")).read() if os.path.exists(os.path.join(d, ".story", "preferences.md")) else ""
    check("#2: approvals, never and unread reach the memory", "Approved once (do not ask again): editer la liste test" in pf
          and "Never without the owner: envoyer de vrais courriels" in pf and "Not read by the import: avant le 14 octobre" in pf)
print("%d failed" % fails); sys.exit(1 if fails else 0)
