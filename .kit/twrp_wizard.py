#!/usr/bin/env python3
"""TWRP Recovery-style clickable terminal GUI installer for the board kit (Python 3 stdlib only).

Supports:
  - True mouse clicking / touch tapping in any modern terminal (XTerm SGR / curses mouse events)
  - Keyboard navigation (Up/Down/Left/Right/Tab, hotkeys 1-9, Space/Enter to select, I to Install, Q to Quit)
  - Automatic Step 0 pre-population:
      * 3 inferred colour palettes (with live ANSI 24-bit / 256-color swatch previews)
      * Autonomy & sprawl profile (Full Autonomous + Auto-Defaults Carry-On, Balanced, or Gated)
      * Runtime / Use Case (Antigravity agy, Claude Code Web, LMArena / Sandbox, Hybrid, Generic CLI)
      * Scope (This Project Only vs All Discovered Projects)
  - Headless / non-TTY mode (--headless or --print-choices) for automated testing and agent chat harnesses.

Usage:
    python3 -I scripts/twrp_wizard.py [TARGET_DIR] [--step0-json /tmp/step0.json] [--dry-run] [--headless]
"""
import argparse
import curses
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import step0_scan  # noqa: E402


def default_selections(profile):
    palettes = profile.get("palettes") or []
    pal_id = next((p["id"] for p in palettes if p.get("recommended")), palettes[0]["id"] if palettes else "ember-classic")

    autonomy_profiles = (profile.get("autonomy_assessment") or {}).get("profiles") or []
    auto_id = next((p["id"] for p in autonomy_profiles if p.get("recommended")), "balanced")

    runtimes = profile.get("runtime_options") or []
    rt_id = next((r["id"] for r in runtimes if r.get("recommended")), profile.get("detected_runtime") or "agy")

    scopes = profile.get("scope_options") or []
    scope_id = next((s["id"] for s in scopes if s.get("recommended")), "single")

    clis = profile.get("cli_options") or []
    cli_id = next((c["id"] for c in clis if c.get("recommended")), "agy")

    return {
        "palette": pal_id,
        "autonomy": auto_id,
        "runtime": rt_id,
        "scope": scope_id,
        "cli": cli_id,
        "trunk_push": "yes" if auto_id == "full_auto" else "no",
    }


def build_install_argv(target_dir, profile, selections, report_path=None, dry_run=False):
    palettes = {p["id"]: p for p in (profile.get("palettes") or [])}
    chosen_pal = palettes.get(selections["palette"]) or (profile.get("palettes") or [None])[0]

    argv = [
        os.path.abspath(target_dir),
        "--project", profile.get("project_name") or os.path.basename(os.path.abspath(target_dir)),
        "--talk", profile.get("inferred_language") or "English",
        "--runtime", selections["runtime"],
        "--autonomy", selections["autonomy"],
        "--scope", selections["scope"],
        "--trunk-push", selections.get("trunk_push") or "no",
    ]
    if selections.get("cli") and selections["runtime"] in ("local", "agy", "both"):
        argv.extend(["--cli", "agy" if selections["runtime"] in ("agy", "both") else selections["cli"]])
    if chosen_pal:
        argv.extend(["--palette", chosen_pal["id"], "--palette-json", json.dumps(chosen_pal, ensure_ascii=False)])
    if report_path and os.path.isfile(report_path):
        argv.extend(["--survey", report_path])
    else:
        argv.extend(["--no-survey", "configured via Step 0 TWRP terminal GUI wizard"])
    if dry_run:
        argv.append("--dry-run")
    return argv


def run_curses_gui(profile, initial_selections):
    """Launch the interactive TWRP Recovery-style clickable curses dashboard."""
    state = dict(initial_selections)
    state["confirmed"] = False
    palettes = profile.get("palettes") or []
    autonomy_profiles = (profile.get("autonomy_assessment") or {}).get("profiles") or []
    runtimes = profile.get("runtime_options") or []
    scopes = profile.get("scope_options") or []

    sections = [
        ("palette", "1. COLOUR PALETTE (Inferred from Step 0 Repo & Chat Logs)", [
            (p["id"], p["name"], "★ REC" if p.get("recommended") else "", p.get("reason", ""),
             "%s / %s" % (p["dark"]["ember"], p["dark"]["question"]))
            for p in palettes
        ]),
        ("autonomy", "2. AUTONOMY & SPRAWL PROFILE (Measured from Past Logs)", [
            (a["id"], a["name"], "★ REC" if a.get("recommended") else "", a.get("summary", ""), "")
            for a in autonomy_profiles
        ]),
        ("runtime", "3. RUNTIME / TARGET USE CASE", [
            (r["id"], r["name"], "★ REC" if r.get("recommended") else "", "", "")
            for r in runtimes
        ]),
        ("cli", "4. YOUR ONE-WORD COMMAND OPENS (local / this computer)", [
            (c["id"], c["name"], "★ REC" if c.get("recommended") else "", "", "")
            for c in (profile.get("cli_options") or [])
        ]),
        ("scope", "5. BOARD SCOPE (%d Discovered Projects)" % len(profile.get("discovered_projects") or []), [
            (s["id"], s["name"], "★ REC" if s.get("recommended") else "", ", ".join(s.get("projects") or []), "")
            for s in scopes
        ]),
    ]

    flat_buttons = []
    for sec_key, _, items in sections:
        for item_id, label, badge, desc, extra in items:
            flat_buttons.append({"sec": sec_key, "id": item_id, "label": label, "badge": badge, "desc": desc, "extra": extra})
    flat_buttons.append({"sec": "_action", "id": "install", "label": "▶ INSTALL & CONFIGURE BOARD", "badge": "ENTER", "desc": "Apply Step 0 profile & install kit", "extra": ""})
    flat_buttons.append({"sec": "_action", "id": "cancel", "label": "✕ CANCEL", "badge": "Q", "desc": "Exit without changes", "extra": ""})

    def _ui(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except Exception:
            pass
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except Exception:
                pass
            curses.init_pair(1, curses.COLOR_CYAN, -1)      # Header / title
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)   # Active / selected tile
            curses.init_pair(3, curses.COLOR_YELLOW, -1)    # ★ REC badge
            curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_GREEN)  # Install button
            curses.init_pair(5, curses.COLOR_WHITE, -1)     # Standard tile

        focus_idx = 0
        hitboxes = []

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            hitboxes = []
            y = 0

            def put(row, col, text, attr=0):
                if 0 <= row < h and 0 <= col < w:
                    clipped = text[: max(0, w - col - 1)]
                    if clipped:
                        try:
                            stdscr.addstr(row, col, clipped, attr)
                        except curses.error:
                            pass

            hdr_attr = curses.color_pair(1) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
            rec_attr = curses.color_pair(3) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD
            sel_attr = curses.color_pair(2) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE
            inst_attr = curses.color_pair(4) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE

            put(y, 1, "╔" + "═" * max(2, w - 4) + "╗", hdr_attr)
            y += 1
            title = " TWRP-STYLE BOARD KIT RECOVERY & STEP 0 INSTALLER — %s " % profile.get("project_name", "Project").upper()
            put(y, 1, "║" + title.center(max(2, w - 4))[: max(2, w - 4)] + "║", hdr_attr)
            y += 1
            tm = profile.get("transcript_metrics") or {}
            sub = " Step 0: %d sessions | %.1f tools/turn | %d%% approvals | %d%% tangents | Lang: %s " % (
                tm.get("sessions_scanned", 0), tm.get("tools_per_user_turn", 0.0),
                int(tm.get("approval_ratio", 0.0) * 100), int(tm.get("tangent_ratio", 0.0) * 100),
                profile.get("inferred_language", "English"),
            )
            put(y, 1, "║" + sub.center(max(2, w - 4))[: max(2, w - 4)] + "║", rec_attr)
            y += 1
            put(y, 1, "╚" + "═" * max(2, w - 4) + "╝", hdr_attr)
            y += 1

            btn_idx = 0
            for sec_key, sec_title, items in sections:
                if y < h - 4:
                    put(y, 2, sec_title, hdr_attr)
                    y += 1
                for item_id, label, badge, desc, extra in items:
                    is_selected = state.get(sec_key) == item_id
                    is_focused = (btn_idx == focus_idx)
                    mark = "(●)" if is_selected else "( )"
                    b_str = (" [%s]" % badge) if badge else ""
                    ex_str = (" {%s}" % extra) if extra else ""
                    box_text = " %s %s%s%s " % (mark, label, ex_str, b_str)
                    attr = sel_attr if is_selected else (curses.A_UNDERLINE | curses.A_BOLD if is_focused else 0)
                    if is_focused:
                        box_text = "▶" + box_text
                    else:
                        box_text = " " + box_text
                    if y < h - 3:
                        put(y, 2, box_text.ljust(max(10, w - 6)), attr)
                        hitboxes.append((y, y, 2, min(w - 2, len(box_text) + 4), btn_idx))
                        y += 1
                        if is_selected and desc and y < h - 3:
                            put(y, 6, "↳ " + desc, rec_attr if badge else 0)
                            y += 1
                    btn_idx += 1
                y += 1

            # Bottom TWRP Action Bar
            act_y = min(h - 2, max(y, h - 3))
            install_idx = len(flat_buttons) - 2
            cancel_idx = len(flat_buttons) - 1
            inst_box = " [ ▶ INSTALL & BOOT BOARD (Enter / Click) ] "
            canc_box = " [ ✕ CANCEL (Q) ] "
            put(act_y, 2, inst_box, inst_attr | (curses.A_UNDERLINE if focus_idx == install_idx else 0))
            hitboxes.append((act_y, act_y, 2, 2 + len(inst_box), install_idx))
            c_col = min(w - len(canc_box) - 2, 4 + len(inst_box) + 2)
            put(act_y, c_col, canc_box, curses.A_REVERSE if focus_idx == cancel_idx else curses.A_BOLD)
            hitboxes.append((act_y, act_y, c_col, c_col + len(canc_box), cancel_idx))

            if act_y + 1 < h:
                put(act_y + 1, 2, "Mouse: Click any button | Keys: ↑/↓/Tab move, Space selects, I/Enter installs, Q quits", 0)

            stdscr.refresh()
            ch = stdscr.getch()

            if ch in (ord("q"), ord("Q"), 27):
                state["confirmed"] = False
                return
            elif ch in (ord("i"), ord("I")):
                state["confirmed"] = True
                return
            elif ch in (curses.KEY_UP, ord("k")):
                focus_idx = (focus_idx - 1) % len(flat_buttons)
            elif ch in (curses.KEY_DOWN, ord("j"), 9):  # Tab
                focus_idx = (focus_idx + 1) % len(flat_buttons)
            elif ch in (ord(" "), 10, 13, curses.KEY_ENTER):
                b = flat_buttons[focus_idx]
                if b["sec"] == "_action":
                    state["confirmed"] = (b["id"] == "install")
                    return
                state[b["sec"]] = b["id"]
                if ch in (10, 13, curses.KEY_ENTER) and b["sec"] == "autonomy":
                    state["trunk_push"] = "yes" if b["id"] == "full_auto" else "no"
            elif ch == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED):
                        for r0, r1, c0, c1, b_i in hitboxes:
                            if r0 <= my <= r1 and c0 <= mx <= c1:
                                focus_idx = b_i
                                b = flat_buttons[b_i]
                                if b["sec"] == "_action":
                                    state["confirmed"] = (b["id"] == "install")
                                    return
                                state[b["sec"]] = b["id"]
                                if b["sec"] == "autonomy":
                                    state["trunk_push"] = "yes" if b["id"] == "full_auto" else "no"
                                break
                except Exception:
                    pass

    curses.wrapper(_ui)
    return state


def render_ansi_summary(profile, selections):
    """Render an ASCII/ANSI TWRP-style summary box for non-interactive or pre-install display."""
    palettes = {p["id"]: p for p in (profile.get("palettes") or [])}
    pal = palettes.get(selections["palette"]) or {}
    lines = [
        "╔══════════════════════════════════════════════════════════════════════════╗",
        "║         TWRP-STYLE STEP 0 BOARD INSTALLER — CONFIGURATION SUMMARY        ║",
        "╠══════════════════════════════════════════════════════════════════════════╣",
        "║ Project:   %-61s ║" % ("%s (%s)" % (profile.get("project_name"), profile.get("inferred_language"))),
        "║ Palette:   %-61s ║" % ("%s [%s]" % (pal.get("name", selections["palette"]), selections["palette"])),
        "║ Autonomy:  %-61s ║" % selections["autonomy"],
        "║ Runtime:   %-61s ║" % selections["runtime"],
        "║ CLI:       %-61s ║" % selections.get("cli", "-"),
        "║ Push main: %-61s ║" % selections.get("trunk_push", "no"),
        "║ Scope:     %-61s ║" % selections["scope"],
        "╚══════════════════════════════════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="TWRP-style clickable terminal GUI installer for the board kit")
    ap.add_argument("target", nargs="?", default=".", help="Target repository directory")
    ap.add_argument("--step0-json", default=None, help="Load existing Step 0 JSON profile instead of scanning")
    ap.add_argument("--home", default=None, help="Override home directory for Step 0 discovery")
    ap.add_argument("--palette", default=None, help="Pre-select colour palette id")
    ap.add_argument("--autonomy", choices=["full_auto", "balanced", "gated"], default=None, help="Pre-select autonomy profile")
    ap.add_argument("--runtime", choices=["local", "agy", "claude", "arena", "generic", "both"], default=None, help="Pre-select runtime")
    ap.add_argument("--cli", choices=["agy", "claude"], default=None, help="Pre-select the CLI the one-word command opens")
    ap.add_argument("--trunk-push", choices=["yes", "no"], default=None,
                    help="The owner's standing permission to push the trunk (headless: 'no' unless given)")
    ap.add_argument("--scope", choices=["single", "all"], default=None, help="Pre-select project scope")
    ap.add_argument("--headless", action="store_true", help="Run non-interactively using Step 0 recommendations + CLI overrides")
    ap.add_argument("--print-choices", action="store_true", help="Print Step 0 profile + recommended selections as JSON and exit")
    ap.add_argument("--dry-run", action="store_true", help="Pass --dry-run to install.py")
    a = ap.parse_args(argv)

    if a.step0_json and os.path.isfile(a.step0_json):
        with open(a.step0_json, encoding="utf-8") as fh:
            profile = json.load(fh)
    else:
        profile = step0_scan.build_step0_profile(a.target, home_dir=a.home)

    selections = default_selections(profile)
    if a.palette:
        selections["palette"] = a.palette
    if a.autonomy:
        selections["autonomy"] = a.autonomy
        selections["trunk_push"] = "yes" if a.autonomy == "full_auto" else "no"
    if a.runtime:
        selections["runtime"] = a.runtime
    if a.scope:
        selections["scope"] = a.scope
    if a.cli:
        selections["cli"] = a.cli
    interactive = not a.headless and sys.stdin.isatty() and sys.stdout.isatty()
    if a.trunk_push:
        selections["trunk_push"] = a.trunk_push
    elif not interactive and not a.print_choices:
        # Nobody saw the screen: pushing the trunk is the owner's permission to give, never a recommendation.
        selections["trunk_push"] = "no"

    if a.print_choices:
        print(json.dumps({"profile": profile, "selections": selections}, indent=2, ensure_ascii=False))
        return 0

    if interactive:
        res = run_curses_gui(profile, selections)
        if not res or not res.get("confirmed"):
            print("Installation cancelled by user.")
            return 1
        selections = res

    print(render_ansi_summary(profile, selections))

    # Write Step 0 survey report into target/survey/<date>/report.md (unless --dry-run)
    date_slug = profile.get("generated_at", "2026-11-20")[:10]
    report_path = os.path.join(os.path.abspath(a.target), "survey", date_slug, "report.md")
    if not a.dry_run:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(step0_scan.render_markdown_report(profile))

    install_argv = build_install_argv(a.target, profile, selections, report_path=report_path if not a.dry_run else None, dry_run=a.dry_run)
    install_py = os.path.join(REPO_ROOT, "install.py")
    r = subprocess.run([sys.executable, "-I", install_py] + install_argv)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
