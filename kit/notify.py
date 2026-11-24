"""Phone notifications (ntfy or any webhook), one rule for both boards: the local server (board_server.py) and the
Artifact (hooks/kit_notify.py, in the session that writes there) notify the same topic through this file.

- What: a post that ENTERS To do or Waiting on you; and, unless autonomy is full_auto, a session's answer (`reply_to`).
  Not a post already there, not a copy that arrives from the other board, not the owner's own post, not a part's "done".
- Once: both boards give the notification the same ntfy id (X-Sequence-ID), so a second one replaces the first; on one
  machine .kit/run/notified.json stops it, and a post written over an hour ago is never announced by a late copy.
- Where a tap goes: the board the post was written on; a button opens the other one. The computer's address is the
  Tailscale funnel's, read from Tailscale and only when it points at this board.
- The topic: $KIT_NOTIFY_URL, else .kit/board_data/notify_url (git-ignored; what the Share panel saves), else
  kit.notify_url. Whoever has it reads the notifications: in a public repo keep it out of the config.
- Privacy: the text says what happened, never the post's, unless kit.notify_detail is true."""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TEXT = {"en": {"todo": "To do", "question": "Waiting on you", "post": "A post needs you.", "replied": "%s replied",
               "answered": "A session answered under a post.", "artifact": "Claude board", "local": "Local board"},
        "fr": {"todo": "À faire", "question": "En attente de toi", "post": "Une publication t'attend.", "replied": "%s a répondu",
               "answered": "Une session a répondu sous une publication.", "artifact": "Tableau Claude", "local": "Tableau local"}}
COLS = ("todo", "question")
FRESH_S = 3600


def settings(root, config=None):
    """What this project says about notifications: {url, detail, project, lang, artifact, board_url, funnel_url, replies}."""
    c = config
    if c is None:
        try:
            with open(os.path.join(root, ".kit", "config.json"), encoding="utf-8") as fh:
                c = json.load(fh)
        except (OSError, ValueError):
            c = {}
    k = c.get("kit") or {}
    talk = str((c.get("language") or {}).get("talk") or "").lower()
    board = str(c.get("board_url") or "").rstrip("/")
    art = str(k.get("artifact_url") or (board if "claude.ai/" in board else "")).rstrip("/")
    auto = k.get("autonomy")
    level = str(auto.get("level") if isinstance(auto, dict) else auto or "")
    return {"url": topic(root, k), "detail": bool(k.get("notify_detail")), "project": str(c.get("project") or "board"),
            "lang": "fr" if talk.startswith("fr") else "en", "artifact": art, "board_url": board,
            "funnel_url": str(os.environ.get("KIT_FUNNEL_URL") or k.get("funnel_url") or "").rstrip("/"),
            "replies": level != "full_auto"}


def topic_file(root):
    return os.path.join(root, ".kit", "board_data", "notify_url")


def topic(root, kit_cfg=None):
    u = os.environ.get("KIT_NOTIFY_URL")
    if u is None:
        try:
            with open(topic_file(root), encoding="utf-8") as fh:
                u = fh.read()                       # saved empty: off on this computer
        except OSError:
            u = (kit_cfg or {}).get("notify_url")
    u = str(u or "").strip()
    return u if u.startswith(("http://", "https://")) else ""


def funnel_target(status, port=None):
    """(address, port) of the funnel's root in `tailscale funnel status --json`, or None. With `port`, only that one."""
    for host, on in (status.get("AllowFunnel") or {}).items():
        h = ((status.get("Web") or {}).get(host) or {}).get("Handlers") or {}
        m = re.match(r"^https?://(?:127\.0\.0\.1|localhost):(\d+)/?$", str((h.get("/") or {}).get("Proxy") or ""))
        if on and m and (port is None or int(m.group(1)) == int(port)):
            name, _, p = host.rpartition(":")
            return "https://" + (name if p == "443" else host), int(m.group(1))
    return None


_funnel = {"at": 0.0, "status": {}}


def funnel_status(max_age=60):
    if time.time() - _funnel["at"] > max_age:
        _funnel["at"] = time.time()
        try:
            r = subprocess.run(["tailscale", "funnel", "status", "--json"], capture_output=True, text=True, timeout=8)
            _funnel["status"] = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
        except (OSError, ValueError, subprocess.SubprocessError):
            _funnel["status"] = {}
    return _funnel["status"]


def links(cfg, post_id, port=None, repo=""):
    """(tap, [(label, url)]): the board written on (the local one when `port` is given, else the Artifact), then the other."""
    frag = "#/post/" + urllib.parse.quote(post_id, safe="") if post_id else ""
    t = TEXT[cfg["lang"]]
    got = funnel_target(funnel_status(), port) if port else None
    base = got[0] if got else cfg["funnel_url"]
    if not base and cfg["board_url"].startswith("http") and "claude.ai/" not in cfg["board_url"] \
            and not re.match(r"^https?://(127\.|localhost|0\.0\.0\.0|\[::1\])", cfg["board_url"]):
        base = cfg["board_url"]                     # a phone cannot open this computer's loopback address
    local = (base + "/" + ("?repo=" + urllib.parse.quote(repo, safe="") if repo else "") + frag) if base else ""
    art = (cfg["artifact"] + (("?repo=" + urllib.parse.quote(repo, safe="")) if repo else "") + frag) if cfg["artifact"] else ""
    both = [(t["local"], local), (t["artifact"], art)]
    if not port:
        both.reverse()
    both = [x for x in both if x[1]]
    return (both[0][1] if both else ""), both[1:]


def _stale(d):
    for k in ("updated_at", "created_at"):
        v = str(d.get(k) or "")
        if v:
            try:
                then = datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return False
            return (datetime.now(timezone.utc) - then).total_seconds() > FRESH_S
    return False


def slug(v):
    return re.sub(r"[^a-z0-9_-]+", "-", str(v or "").lower()).strip("-")


def event(cfg, coll, doc_id, d, prev=None):
    """What this write means to the owner's phone: {kind, post, seq, title, body, repo} or None. `prev`: the post's lane
    before it (None: not known, or a new post)."""
    if not isinstance(d, dict) or d.get("removed"):
        return None
    m = re.match(r"^(?:repos/([^/]+)/)?(posts|comments)$", str(coll or ""))
    if not m:
        return None
    repo, coll = m.group(1) or "", m.group(2)
    t, doc_id = TEXT[cfg["lang"]], str(doc_id or "")
    own = not repo or repo == slug(cfg["project"])          # this project's board, also when it lives under repos/<itself>
    name = cfg["project"] if own else "%s/%s" % (cfg["project"], repo)
    if coll == "posts":
        col = d.get("column")
        if col not in COLS or doc_id.startswith("_") or col == prev or d.get("from") == "owner" or _stale(d):
            return None
        body = (str(d.get("title") or doc_id) + "\n" + str(d.get("body") or "")[:240]) if cfg["detail"] else t["post"]
        post, title = doc_id, "%s: %s" % (name, t[col])
    else:
        if not cfg["replies"] or not d.get("reply_to") or d.get("like") or \
                str(d.get("from") or "") in ("", "owner", "viewer", "clerk") or _stale(d):
            return None
        post, col = str(d.get("post_id") or ""), "reply"
        title = "%s: %s" % (name, t["replied"] % d.get("from"))
        body = str(d.get("text") or "")[:240] if cfg["detail"] else t["answered"]
    seq = "kit" + hashlib.sha256("|".join((slug(cfg["project"]) if own else repo, coll, doc_id, str(col))).encode()).hexdigest()[:24]
    return {"kind": col, "post": post, "repo": repo, "seq": seq, "title": title, "body": body}


def claim(root, seq, always=False):
    """True when nothing on this machine announced `seq` in the last hour (and records that it is now)."""
    p = os.path.join(root, ".kit", "run", "notified.json")
    try:
        with open(p, encoding="utf-8") as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    now = time.time()
    seen = {k: v for k, v in seen.items() if isinstance(v, (int, float)) and now - v < FRESH_S} if isinstance(seen, dict) else {}
    if seq in seen and not always:
        return False
    seen[seq] = now
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(seen, fh)
        os.replace(p + ".tmp", p)
    except OSError:
        pass
    return True


def forget(root, seq):
    """A notification that did not leave: the next writer may send it."""
    p = os.path.join(root, ".kit", "run", "notified.json")
    try:
        with open(p, encoding="utf-8") as fh:
            seen = json.load(fh)
        if seen.pop(seq, None) is not None:
            with open(p + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(seen, fh)
            os.replace(p + ".tmp", p)
    except (OSError, ValueError, AttributeError):
        pass


def _h(v):
    return str(v).replace("\n", " ").replace("\r", " ").encode("utf-8").decode("latin-1")


def send(url, title, body, tap="", buttons=(), seq="", again=True):
    """None when it left, else why not. One retry on 429: anonymous ntfy.sh limits by IP, and cloud sessions share theirs."""
    hdr = {"Title": _h(title[:120]), "Content-Type": "text/plain; charset=utf-8", "Tags": "clipboard"}
    if str(tap).startswith(("http://", "https://")):
        hdr["Click"] = _h(tap)
    acts = ["view, %s, %s" % (lb, u.replace(",", "%2C").replace(";", "%3B")) for lb, u in buttons][:3]
    if acts:
        hdr["Actions"] = _h("; ".join(acts))
    if seq:
        hdr["X-Sequence-ID"] = seq
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body.encode("utf-8"), headers=hdr, method="POST"), timeout=5):
            return None
    except urllib.error.HTTPError as e:
        if e.code == 429 and again:
            time.sleep(3)
            return send(url, title, body, tap, buttons, seq, again=False)
        return "HTTP %d" % e.code
    except (OSError, ValueError) as e:
        return type(e).__name__


def announce(root, cfg, coll, doc_id, d, prev=None, port=None, repo="", known=False):
    """Send what this write means, once. Returns (sent, why not). `known`: the lane before is certain (a real move, so
    an earlier notification of the same post does not silence it)."""
    if not cfg.get("url"):
        return False, ""
    e = event(cfg, ("repos/%s/%s" % (repo, coll)) if repo else coll, doc_id, d, prev)
    if not e or not claim(root, e["seq"], always=known and prev is not None):
        return False, ""
    tap, buttons = links(cfg, e["post"], port=port, repo=e["repo"])
    why = send(cfg["url"], e["title"], e["body"], tap, buttons, e["seq"])
    if why:
        forget(root, e["seq"])
        return False, why
    return True, ""
