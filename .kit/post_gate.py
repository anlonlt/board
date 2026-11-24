"""What a post or a comment an agent writes must be, checked by the script that writes it (board_cli.py), not by a guide.

- A post's lane follows what it is: options or kind question -> question (he chooses), minutes or runner owner -> todo (he
  does something); an unknown lane is refused. Nothing he must choose or do waits in the news lane or in a thread.
- An agent's comment never asks him for a choice (a Like, "should I", "which one"): that is a question post (`kit.py ask`).
- Under `full_auto` a question says which of the few things only he can give it needs.
- A step is complete only with the proof `kit.py done` left on it, for the very fields it proved.
The owner's own writes (the page) do not come through here. Wording is matched in English; a Like's sign in any language.
"""
import hashlib
import json
import re

LANES = ("todo", "question", "info", "done")
NEEDS = {"senses": "his ears, eyes or phone", "taste": "a taste only he can judge", "money": "money", "licence": "a licence or a gate",
         "direction": "a change of direction", "delete": "deleting something of his", "access": "an account or access only he has"}
PROVED = ("verdict", "bars", "check", "breaks", "asked", "not_checked", "artifacts", "body")
ASK = re.compile("|".join((
    "\U0001F44D", r"thumbs?[ -]?up", r"\b(?:a|your) like (?:here|on|below|means|launches)\b", r"\blike (?:this|it|here) (?:to|and|if)\b",
    r"\b(?:should|shall|may) I\b[^.?!\n]*\?", r"\b(?:do you|would you(?: like)?) want\b[^.?!\n]*\?", r"\bwant me to\b",
    r"\bis it ok(?:ay)? (?:if|to)\b", r"\btell me which\b", r"\blet me know (?:which|if you(?:'d| would)? (?:want|like)|whether)\b",
    r"^\s*(?:\*\*)?question(?:\*\*)?\s*:", r"\bon your (?:yes|go)\b")), re.I | re.M)
QUOTED = re.compile(r"^\s*>.*$|```.*?```|`[^`\n]*`|\"[^\"\n]*\"|\u201c[^\u201d\n]*\u201d", re.M | re.S)


def autonomy(cfg):
    a = ((cfg or {}).get("kit") or {}).get("autonomy") or {}
    if a.get("auto_defaults") or a.get("carry_on"):
        return "full_auto"
    return str(a.get("level") or "balanced")


def autonomy_lines(cfg):
    lvl = autonomy(cfg)
    if lvl == "full_auto":
        return ["- `full_auto`: do it, don't ask. Launch the next step, act on a review's continue or redirect, apply a question's default "
                "at its deadline, and carry on with all that does not wait.",
                "- Ask only for: %s. Then ONE question post (`python3 -I .kit/kit.py ask --needs %s ...`); never a Like under a message, "
                "never a question in a thread (both are refused)." % ("; ".join(NEEDS.values()), "|".join(NEEDS)),
                "- A side idea of his becomes a planned step, not a detour from the train that runs."]
    if lvl == "gated":
        return ["- `gated`: wait for his answer on each question or todo post before you launch a part or apply a choice; ask with "
                "`python3 -I .kit/kit.py ask`, never in a thread."]
    return ["- `balanced`: launch what he said yes to; a choice of his is a question post (`python3 -I .kit/kit.py ask`), never a Like "
            "asked for under a message; a side idea becomes a planned step. (Change it: `install.py . --update --autonomy full_auto|gated`.)"]


def proof_key(p):
    """What `kit.py done` proved, as one key: any of these fields changed since, and the proof is void."""
    return hashlib.sha256(json.dumps({k: p.get(k) for k in PROVED}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def proved(p):
    pr = p.get("proof")
    return isinstance(pr, dict) and pr.get("key") == proof_key(p)


def is_step(p):
    return bool(p.get("train") and p.get("part")) and p.get("kind") not in ("question", "issue")


def is_complete(p):
    return str(p.get("status") or "").strip().lower() in ("complete", "completed", "done")


def check_post(doc_id, new, old=None, cfg=None):
    """(data to write, notes, error or None). `new`: the fields written; `old`: the post as it is (None: new)."""
    notes = []
    if str(doc_id).startswith("_") or not isinstance(new, dict):
        return new, notes, None
    old = old or {}
    new, p = dict(new), dict(old, **new)
    kit = str(p.get("from") or "").startswith("kit ")            # the kit's own posts (an import, the web environment)
    fresh = lambda k: bool(p.get(k)) and not old.get(k)          # noqa: E731 - given by this write
    want = None
    if fresh("options") or (p.get("kind") == "question" and not old):
        want = "question"
    elif fresh("minutes") or (p.get("runner") == "owner" and not old):
        want = "todo"
    if want and p.get("column") not in (want, "done"):
        if p.get("column"):
            notes.append("lane: %s -> %s (%s)" % (p["column"], want, "he chooses" if want == "question" else "he does something"))
        new["column"] = p["column"] = want
    if not old and not p.get("column"):
        new["column"] = p["column"] = "info"
    if "column" in new and p["column"] not in LANES:
        return new, notes, "lane %r does not exist: %s" % (p["column"], ", ".join(LANES))
    if p.get("column") == "question" and old.get("column") != "question" and not kit and not is_step(p):
        if not p.get("default_option") and not p.get("default"):
            return new, notes, ("a question has a default (`default_option`), used when he does not answer: `python3 -I .kit/kit.py ask "
                                "--title ... --option A --option B --default A --deadline <date>` writes it whole")
        if autonomy(cfg) == "full_auto" and p.get("needs") not in NEEDS:
            return new, notes, ("full_auto: apply the default and go on. A question is only for %s: name which (`needs`: %s)"
                                % ("; ".join(NEEDS.values()), ", ".join(NEEDS)))
    if is_step(p) and is_complete(p) and not is_complete(old) and not proved(p):
        return new, notes, ("a step is complete only after `python3 -I .kit/kit.py done %s` says OK on the post as it is now "
                            "(its check passes, its break makes it fail, his comments are answered)" % doc_id)
    return new, notes, None


def check_comment(doc_id, c, post=None):
    """Why this agent's comment is refused, or None."""
    if not isinstance(c, dict) or str(c.get("from") or "owner") == "owner" or str(c.get("from")).startswith("kit"):
        return None
    if str(doc_id).startswith("welcome-") or c.get("kind") == "review" or (post or {}).get("column") == "question":
        return None
    hit = ASK.search(QUOTED.sub("", str(c.get("text") or "")))
    if not hit:
        return None
    return ("this asks him in a thread (%r): a choice of his is a question post: `python3 -I .kit/kit.py ask --title ... --option A "
            "--option B --default A --deadline <date>`, and this comment points to it. A step in his direction needs no asking: do it, "
            "then say so." % hit.group(0).strip()[:60])
