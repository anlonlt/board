"""Test of board_typing.py: python3 -I .claude/hooks/test_board_typing.py"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "board_typing.py")


def run(prompt):
    p = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps({"prompt": prompt}), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"] if p.stdout.strip() else None


def main():
    a = run('Board comment from the owner on your post ED4-editor-tutorial, thread t1, 09:41, verbatim: "hi". Answer him.')
    assert a and "doc_id `ED4-editor-tutorial`" in a, a
    b = run('Board comment from the owner with @o, on post _orchestrator, thread x, 09:44 UTC, verbatim: "q"')
    assert b and "doc_id `_orchestrator`" in b, b
    assert run("Done. Check post ED4-editor-tutorial.") is None
    assert run("on post X, a comment from the owner") is None
    assert run("") is None
    print("board_typing: 5 cases OK")


if __name__ == "__main__":
    main()
