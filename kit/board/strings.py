#!/usr/bin/env python3
"""The board page's visible text as one table, so a translation is a small JSON file, not 146 KB of hand edits
(issue #3 item 4, 2026-11-20).

  python3 -I kit/board/strings.py extract board.template.html > strings.en.json   (the kit's maintainers, after a page change)
  python3 -I kit/board/strings.py template <project>/.kit/strings.json             (an empty translation to fill)

install.py applies `.kit/strings.json` ({"language": "French", "strings": {"<English>": "<translation>", ...}}) to the
rendered page at every install and update, so the page is never edited by hand and an update never shows it as KEPT.
Only text the person sees is replaced: HTML text between tags, the placeholder / aria-label / title attributes, and
whole JavaScript string literals listed in the table that are not compared, keyed or used as class names.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(HERE, "strings.en.json")
ATTRS = ("placeholder", "aria-label", "title")
# Literals that look like words but are code (class names, keys, API names) or comment fragments: never translated.
CODE = re.compile(r"^(?:[a-z][a-z0-9_-]*(?: [a-z][a-z0-9_-]*)*|use strict|IntersectionObserver|Enter|txt md)$")
PROTOCOL_LITERALS = {"Answer: ", "\U0001f44d Like: he approves this", " [post ", "_orchestrator"}


def split(page):
    i = page.index("<script>")
    j = page.index("</script>", i)
    return page[:i], page[i:j], page[j:]


def js_literals(js):
    """(text, quote) of every string literal in the script, comments skipped."""
    out, i, n = [], 0, len(js)
    while i < n:
        c = js[i]
        if js.startswith("//", i):
            i = js.find("\n", i)
            i = n if i < 0 else i
        elif js.startswith("/*", i):
            i = js.find("*/", i + 2)
            i = n if i < 0 else i + 2
        elif c in "\"'":
            j = i + 1
            while j < n and js[j] != c and js[j] != "\n":
                j += 2 if js[j] == "\\" else 1
            out.append((js[i + 1:j], c, i))
            i = j + 1
        elif c == "/" and re.match(r"[=(,:!&|?{};\s]", js[max(0, i - 1)] if i else " ") and not js.startswith("/ ", i):
            # a regular expression literal: skip to its closing slash
            j = i + 1
            while j < n and js[j] != "/" and js[j] != "\n":
                j += 2 if js[j] == "\\" else 1
            i = j + 1
        else:
            i += 1
    return out


def decode_js(s):
    """Decode JS string escapes (\\uXXXX, \\n, \\', \\") into plain text; leaves already-plain text as-is."""
    if not isinstance(s, str) or "\\" not in s:
        return s
    try:
        inner = s.replace("\\'", "'")
        # Escape any unescaped double quotes before passing to json.loads
        inner = re.sub(r'(?<!\\)"', r'\"', inner)
        return json.loads('"' + inner + '"')
    except Exception:
        return s


def encode_js(s, q='"'):
    """Encode plain text as the interior of a JS string literal delimited by q ('"' or "'")."""
    esc = json.dumps(s, ensure_ascii=False)[1:-1]
    if q == "'":
        esc = esc.replace("'", "\\'").replace('\\"', '"')
    return esc


def code_use(js, lit, q, at, end):
    before = js[max(0, at - 6):at]
    after = js[end:end + 6]
    if re.search(r"\?\s*$", before):
        return False
    return bool(re.search(r"(?:[=!]==?|case)\s*$", before) or re.match(r"\s*(?:[=!]==?|:(?!:))", after)
                or re.search(r"(?:classList\.\w+\(|className\s*=\s*|querySelector(?:All)?\(|getElementById\()\s*$", before))


def extract(page):
    head, js, tail = split(page)
    html = re.sub(r"<style.*?</style>", "", head + tail, flags=re.S)
    rows, seen = [], set()

    def add(en, kind):
        if re.fullmatch(r"[\w./-]+\.(?:md|py|json)", en):  # a file name in <code>: not translated
            return
        if (en, kind) not in seen and re.search(r"[A-Za-z]{2}", en) and "{{" not in en and not en.startswith("&"):
            seen.add((en, kind))
            rows.append({"en": en, "kind": kind})
    for m in re.findall(r">\s*([^<>]*?[A-Za-z][^<>]*?)\s*<", html):
        add(m, "html")
    for m in re.findall(r'(?:%s)="([^"]*)"' % "|".join(ATTRS), html):
        add(m, "attr")
    for lit, q, at in js_literals(js):
        if not re.search(r"[A-Za-z]{2}", lit) or CODE.match(lit.strip()) or re.search(r"[{}<>;=#]|px\b|^\.|\(\)", lit):
            continue
        if code_use(js, lit, q, at, at + len(lit) + 2):
            continue
        dec = decode_js(lit)
        if dec in PROTOCOL_LITERALS:
            continue
        add(dec, "js")
    return rows


def apply(page, table, strings):
    """The page with each table entry that has a translation replaced in its own kind of place; (page, used, missing)."""
    head, js, tail = split(page)
    used, missing = 0, []
    norm_strings = {}
    for k_raw, v_raw in (strings or {}).items():
        if v_raw:
            norm_strings[decode_js(k_raw)] = decode_js(v_raw)
    never = {decode_js(e) for e in EXCLUDED}  # code tokens and text for Claude: kept as is even if a translation names them
    for row in table:
        en, kind = decode_js(row["en"]), row["kind"]
        if kind == "js" and en in never:
            used += 1  # found and deliberately kept (e.g. "posts/" in removeDocs(["posts/" + id]) is a db path)
            continue
        tr = norm_strings.get(en)
        if not tr or tr == en:
            missing.append(en)
            continue
        if kind == "html":
            pat = re.compile(r"(>\s*)" + re.escape(en) + r"(\s*<)")
            head, a = pat.subn(lambda m: m.group(1) + tr + m.group(2), head)
            tail, b = pat.subn(lambda m: m.group(1) + tr + m.group(2), tail)
            k = a + b
        elif kind == "attr":
            pat = re.compile(r'((?:%s)=")' % "|".join(ATTRS) + re.escape(en) + '"')
            safe = tr.replace('"', "&quot;")
            head, a = pat.subn(lambda m: m.group(1) + safe + '"', head)
            tail, b = pat.subn(lambda m: m.group(1) + safe + '"', tail)
            k = a + b
        else:
            k, out, last = 0, [], 0
            for lit, q, at in js_literals(js):
                if decode_js(lit) != en or code_use(js, lit, q, at, at + len(lit) + 2):
                    continue
                esc = encode_js(tr, q)
                out.append(js[last:at + 1] + esc)
                last = at + 1 + len(lit)
                k += 1
            js = "".join(out) + js[last:]
        used += k > 0
    return head + js + tail, used, missing


# Found by `extract` but never translated: code tokens, and text that goes to Claude (agents read it in English).
EXCLUDED = ["_orchestrator", "2-digit", "/_blob/", "https://", "comments/", "posts/", "groups:", "gallery:3", "board-draft:",
            " [post ", "] image ", "[post ", "[repo ", "[image ", "[discussion ", "_blank", "data/users/", "/board-seen",
            "(hover: hover) and (pointer: fine)", "-posts-", " at /_blob/", ") at /_blob/", "  [attached: ",
            "\n[attached: ", "(in reply to the comment ", "👍", "Answer: ", "Answer: X",
            "👍 Like: he approves this", ", no words.", "👍 Like on the comment of ", " («",
            "»): he agrees, no words.", "(see the attached file)", ": «", "…»", " UTC", "») ",
            "repos/", "[data-seen-post]", "MutationObserver"]


def extract_exclusions():
    extra = [
        ") rotate(", "-6deg", "6deg", "-7deg", "7deg", "deg)", "translateX(",
        "transform .22s ease, opacity .22s ease",
        "transform .42s cubic-bezier(.22, .85, .32, 1), opacity .42s ease",
        "application/octet-stream",
        "button, textarea, input, summary, a",
        "a, button, summary, details, input, textarea",
        "button, a, textarea, input",
        "header.top",
        "header.top h1",
        "posts/_board_meta",
        "\U0001f44d Like: he approves this post, no words.",
    ]
    return [decode_js(e) for e in EXCLUDED] + extra + [
        e for e in (r["en"] for r in extract(open(os.path.join(HERE, "board.template.html"), encoding="utf-8").read()))
        if e.startswith("var(")
    ]


def load_table():
    with open(TABLE, encoding="utf-8") as fh:
        rows = json.load(fh)
    for r in rows:
        if r.get("kind") == "js":
            r["en"] = decode_js(r["en"])
    return rows


def main(argv):
    if len(argv) >= 2 and argv[0] == "extract":
        # CL12: without the exclusions a regenerated table held "posts/" and "comments/", and a translated board wrote a
        # removal to "<translated>posts/P-a" (test_page.js on the translated page)
        out = set(extract_exclusions())
        rows = [r for r in extract(open(argv[1], encoding="utf-8").read()) if not (r["kind"] == "js" and r["en"] in out)]
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    if len(argv) >= 2 and argv[0] == "template":
        t = {"language": "<the owner's language>", "strings": {r["en"]: "" for r in load_table()}}
        with open(argv[1], "w", encoding="utf-8") as fh:
            json.dump(t, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        print("wrote %s: %d strings to translate" % (argv[1], len(t["strings"])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
