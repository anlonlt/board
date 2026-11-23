"""Design principle 1 (docs/DESIGN-PRINCIPLES.md): zero emoji in the board's UI.

Scans every string the page can draw in kit/board/board.template.html (HTML text and attributes, CSS `content:`,
JavaScript string and template literals) and in kit/board/shim.js (its JavaScript literals, which hold the shim's
HTML and CSS too), after decoding \\uXXXX, \\u{...}, HTML entities and CSS escapes, so an emoji cannot hide behind
an escape. Comments and regular expressions are not scanned: they are never drawn.

The only emoji allowed are protocol text that is stored or sent to a session, never shown: the stored Like row
("👍") and the Like messages the clerk and `board_cli.py inbox` parse ("👍 Like: ..."). They are listed in PROTOCOL.

Run: python3 -m unittest tests.test_no_emoji -v
     python3 -I tests/test_no_emoji.py --list   (prints every hit, allowed or not)
"""
import html
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "kit", "board", "board.template.html")
SHIM = os.path.join(ROOT, "kit", "board", "shim.js")

PROTOCOL = {
    "\U0001f44d",
    "\U0001f44d Like: he approves this",
    "\U0001f44d Like: he approves this post, no words.",
    "\U0001f44d Like on the comment of ",
}

_EXTRA = {0x231A, 0x231B, 0x2328, 0x23CF, 0x23E9, 0x23EA, 0x23EB, 0x23EC, 0x23ED, 0x23EE, 0x23EF, 0x23F0, 0x23F1,
          0x23F2, 0x23F3, 0x23F8, 0x23F9, 0x23FA, 0x2B05, 0x2B06, 0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55, 0x2934,
          0x2935, 0x3030, 0x303D, 0x3297, 0x3299, 0x203C, 0x2049, 0x2139, 0x24C2, 0xFE0F, 0x200D}


def is_emoji(ch):
    """Pictographs (U+1F000-1FAFF), Misc Symbols and Dingbats (U+2600-27BF, which holds the check marks used as
    icons), the emoji-presentation symbols elsewhere, and the emoji joiners (VS16, ZWJ)."""
    o = ord(ch)
    return 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or o in _EXTRA


def decode(s):
    s = re.sub(r"\\u\{([0-9a-fA-F]+)\}", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})",
               lambda m: (chr(int(m.group(1), 16)) + chr(int(m.group(2), 16))).encode("utf-16", "surrogatepass").decode("utf-16"), s)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\\\?([0-9a-fA-F]{4,6})(?![0-9a-fA-F])", lambda m: _css(m.group(1)), s)  # CSS: "\1F44D"
    return html.unescape(s)


def _css(h):
    o = int(h, 16)
    return chr(o) if 0x2000 <= o <= 0x10FFFF and not 0xD800 <= o <= 0xDFFF else "\\" + h


def js_literals(js):
    """Every "..." '...' `...` literal of a script (its interior, undecoded), skipping comments and regexes."""
    out, i, n = [], 0, len(js)
    while i < n:
        c = js[i]
        if js.startswith("//", i):
            j = js.find("\n", i)
            i = n if j < 0 else j
        elif js.startswith("/*", i):
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'`":
            j = i + 1
            while j < n and js[j] != c and (c == "`" or js[j] != "\n"):
                j += 2 if js[j] == "\\" else 1
            out.append(js[i + 1:j])
            i = j + 1
        elif c == "/" and re.match(r"[=(,:!&|?{};\s]", js[i - 1] if i else " ") and not js.startswith("/ ", i):
            j = i + 1
            in_class = False
            while j < n and js[j] != "\n" and (in_class or js[j] != "/"):
                if js[j] == "\\":
                    j += 1
                elif js[j] == "[":
                    in_class = True
                elif js[j] == "]":
                    in_class = False
                j += 1
            i = j + 1
        else:
            i += 1
    return out


def drawable(path):
    """(where, decoded text) for each piece of the file that can reach the screen."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if path.endswith(".js"):
        return [("js literal", decode(t)) for t in js_literals(src)]
    pieces = []
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", src, re.S)
    for js in scripts:
        pieces += [("js literal", decode(t)) for t in js_literals(js)]
    rest = re.sub(r"<script\b[^>]*>.*?</script>", "", src, flags=re.S)
    for css in re.findall(r"<style\b[^>]*>(.*?)</style>", rest, re.S):
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        pieces += [("css content", decode(v)) for v in re.findall(r"content\s*:\s*([^;}]+)", css)]
    rest = re.sub(r"<style\b[^>]*>.*?</style>", "", rest, flags=re.S)
    rest = re.sub(r"<!--.*?-->", "", rest, flags=re.S)
    pieces.append(("html", decode(rest)))
    return pieces


def hits(path):
    """(where, text, allowed) for every drawable piece holding an emoji."""
    out = []
    for where, text in drawable(path):
        if any(is_emoji(ch) for ch in text):
            out.append((where, text, text in PROTOCOL))
    return out


class TestNoEmoji(unittest.TestCase):
    def test_zero_emoji_in_ui_strings(self):
        for path in (TEMPLATE, SHIM):
            bad = [(w, t[:80]) for w, t, ok in hits(path) if not ok]
            self.assertEqual(bad, [], "%s draws emoji (principle 1, docs/DESIGN-PRINCIPLES.md)" % os.path.relpath(path, ROOT))

    def test_the_scan_can_fail(self):
        """A planted 👍 behind each escape form is found, so a pass above means something."""
        planted = [
            ('<button>\U0001f44d</button>', "html"),
            ('<span title="&#x1F44D;">x</span>', "entity"),
            ('<script>var a = "\\uD83D\\uDC4D Like";</script>', "surrogate escape"),
            ('<script>var a = `\\u{1F44D} ok`;</script>', "code point escape"),
            ('<script>var a = "\\u2713 Done";</script>', "check mark"),
            ('<style>.x::before { content: "\\1F44D"; }</style>', "css escape"),
        ]
        import tempfile
        for snippet, name in planted:
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
                f.write(snippet)
            try:
                self.assertTrue([h for h in hits(f.name) if not h[2]], "planted emoji not found: " + name)
            finally:
                os.unlink(f.name)

    def test_comments_and_regexes_are_not_drawn(self):
        js = '/* \U0001f44d */ var r = /[\\u{1F300}-\\u{1FAFF}]/gu; // \u2713\nvar s = "plain";'
        self.assertEqual(js_literals(js), ["plain"])


if __name__ == "__main__":
    if "--list" in sys.argv:
        for p in (TEMPLATE, SHIM):
            for w, t, ok in hits(p):
                print("%s  %-10s %-8s %r" % (os.path.relpath(p, ROOT), w, "allowed" if ok else "EMOJI", t[:90]))
        sys.exit(0)
    unittest.main()
