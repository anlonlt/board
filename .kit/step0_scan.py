#!/usr/bin/env python3
"""Cross-platform Step 0 scanner & behavioral/aesthetic profiler (Python 3 stdlib only).

Scans local agy transcripts (~/.gemini/antigravity-cli/brain/*/transcript.jsonl),
Claude Code transcripts (~/.claude/projects/), and local git repositories to produce
a structured Step 0 profile JSON and optional Markdown report:
  1. Discovered projects & scope recommendation (one board for all projects vs this project).
  2. Behavioral Autonomy vs Handholding metrics (tool calls per user turn, short approval rate).
  3. Completion Follow-Through vs Feature Sprawl / Decision Backlog metrics (recommending
     auto_defaults + carry_on when sprawl or waiting backlog is high).
  4. 3 inferred colour palettes (light & dark CSS variables for board.html) based on
     project CSS/theme files and aesthetic hints in conversation logs.

Usage:
    python3 -I scripts/step0_scan.py [TARGET_DIR] [--out-json /tmp/step0.json] [--out-report survey/report.md]
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

APPROVAL_RE = re.compile(
    r"^\s*(?:yes|yep|yeah|ok|okay|oui|d'accord|vas-y|continue|go|go ahead|do it|push|proceed|sounds good|lgtm|👍|\.)\s*[.!]?\s*$",
    re.I,
)
TANGENT_RE = re.compile(
    r"\b(?:also|by the way|btw|another thing|while you're at it|i wonder|what if we|let's also|could we also)\b",
    re.I,
)
HEX_COLOR_RE = re.compile(r"#([0-9a-fA-F]{6})\b")
CSS_VAR_RE = re.compile(r"--([a-zA-Z0-9_-]+)\s*:\s*(#[0-9a-fA-F]{6})\b")

BUILTIN_PALETTES = {
    "ember-classic": {
        "id": "ember-classic",
        "name": "Ember & Ink (Editorial Warm)",
        "reason": "Warm editorial serif contrast with ember accents (#b8561c / #e8894a); balanced for long reading.",
        "light": {
            "bg": "#eef2f6", "surface": "#ffffff", "sunk": "#e2e8ef", "ink": "#1b2433", "muted": "#5b6678",
            "line": "#cfd7e1", "ember": "#b8561c", "ember-ink": "#ffffff", "good": "#2f7a4f", "warn": "#a33b2b",
            "todo": "#b8561c", "question": "#6a4fb3", "info": "#2b6c9e", "done": "#2f7a4f",
        },
        "dark": {
            "bg": "#111826", "surface": "#1a2335", "sunk": "#0c121d", "ink": "#e6ebf2", "muted": "#9aa6b8",
            "line": "#2c3850", "ember": "#e8894a", "ember-ink": "#1a1206", "good": "#6cc492", "warn": "#f08a78",
            "todo": "#e8894a", "question": "#a993ee", "info": "#78b4e3", "done": "#6cc492",
        },
    },
    "oled-cyber": {
        "id": "oled-cyber",
        "name": "Terminal OLED (Sky & Indigo)",
        "reason": "True-black OLED dark surface (#090d16) with high-contrast sky blue (#38bdf8) and emerald (#34d399) for mobile/terminal clarity.",
        "light": {
            "bg": "#f0f4f8", "surface": "#ffffff", "sunk": "#e1e8f0", "ink": "#0f172a", "muted": "#475569",
            "line": "#cbd5e1", "ember": "#0284c7", "ember-ink": "#ffffff", "good": "#059669", "warn": "#dc2626",
            "todo": "#0284c7", "question": "#7c3aed", "info": "#2563eb", "done": "#059669",
        },
        "dark": {
            "bg": "#090d16", "surface": "#111827", "sunk": "#05080f", "ink": "#f1f5f9", "muted": "#94a3b8",
            "line": "#1e293b", "ember": "#38bdf8", "ember-ink": "#082f49", "good": "#34d399", "warn": "#f87171",
            "todo": "#38bdf8", "question": "#a78bfa", "info": "#60a5fa", "done": "#34d399",
        },
    },
    "forest-sage": {
        "id": "forest-sage",
        "name": "Botanical Sage & Gold",
        "reason": "Calm low-strain sage greens (#1f6f54 / #52b788) and warm amber highlights for focused, distraction-free review.",
        "light": {
            "bg": "#f1f5f2", "surface": "#ffffff", "sunk": "#e3ebe5", "ink": "#16241c", "muted": "#52665a",
            "line": "#cad6ce", "ember": "#1f6f54", "ember-ink": "#ffffff", "good": "#2b8a3e", "warn": "#b9382c",
            "todo": "#c46d1e", "question": "#5e4b8b", "info": "#1f6f54", "done": "#2b8a3e",
        },
        "dark": {
            "bg": "#0d1712", "surface": "#15241c", "sunk": "#08100c", "ink": "#e6f2eb", "muted": "#8fa899",
            "line": "#243b2f", "ember": "#52b788", "ember-ink": "#081c15", "good": "#74c69d", "warn": "#f28482",
            "todo": "#f4a261", "question": "#b8a1e3", "info": "#52b788", "done": "#74c69d",
        },
    },
    "amethyst-night": {
        "id": "amethyst-night",
        "name": "Amethyst & Rose Studio",
        "reason": "Deep studio plum and violet accents (#7c3aed / #c084fc) inferred from creative/design preferences.",
        "light": {
            "bg": "#f5f3f9", "surface": "#ffffff", "sunk": "#eae6f3", "ink": "#1e162b", "muted": "#625874",
            "line": "#d5cde4", "ember": "#6d28d9", "ember-ink": "#ffffff", "good": "#15803d", "warn": "#be123c",
            "todo": "#d97706", "question": "#6d28d9", "info": "#2563eb", "done": "#15803d",
        },
        "dark": {
            "bg": "#120e1c", "surface": "#1c162b", "sunk": "#0b0812", "ink": "#efeaf6", "muted": "#a396b8",
            "line": "#312747", "ember": "#c084fc", "ember-ink": "#1e0b36", "good": "#4ade80", "warn": "#fb7185",
            "todo": "#fbbf24", "question": "#c084fc", "info": "#60a5fa", "done": "#4ade80",
        },
    },
}


def _hex_luminance(hex_code):
    h = hex_code.lstrip("#")
    if len(h) != 6:
        return 0.5
    r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex_saturation(hex_code):
    h = hex_code.lstrip("#")
    if len(h) != 6:
        return 0.0
    r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0
    l = (mx + mn) / 2.0
    d = mx - mn
    return d / (2.0 - mx - mn) if l > 0.5 else d / (mx + mn)


def discover_projects(target_dir, home_dir=None):
    """Find git projects in target_dir and sibling directories under home_dir."""
    home = os.path.abspath(home_dir or os.path.expanduser("~"))
    target = os.path.abspath(target_dir)
    candidates = [target]
    parent = os.path.dirname(target)
    for root_cand in (parent, home):
        if os.path.isdir(root_cand):
            try:
                for entry in sorted(os.listdir(root_cand)):
                    if entry.startswith("."):
                        continue
                    full = os.path.join(root_cand, entry)
                    if os.path.isdir(full) and full not in candidates:
                        candidates.append(full)
            except OSError:
                pass
    projects = []
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        is_git = os.path.exists(os.path.join(c, ".git"))
        has_memory = os.path.isdir(os.path.join(c, ".story")) or os.path.isdir(os.path.join(c, ".board", "memory"))
        has_code = any(os.path.exists(os.path.join(c, f)) for f in ("package.json", "pyproject.toml", "README.md", "CLAUDE.md", "AGENTS.md", "install.py"))
        if is_git or has_memory or (c == target and has_code):
            awaits_count = 0
            workstreams = []
            for mdir in (".board/memory", ".story"):
                aw = os.path.join(c, mdir, "awaits.md")
                if os.path.isfile(aw):
                    try:
                        with open(aw, encoding="utf-8") as fh:
                            awaits_count += sum(1 for l in fh if l.strip().startswith("- "))
                    except OSError:
                        pass
                st_dir = os.path.join(c, mdir, "state")
                if os.path.isdir(st_dir):
                    for sf in sorted(glob.glob(os.path.join(st_dir, "*.md"))):
                        workstreams.append(os.path.basename(sf)[:-3])
            projects.append({
                "name": os.path.basename(c) or "project",
                "path": c,
                "is_target": c == target,
                "is_git": is_git,
                "workstreams": workstreams,
                "awaits_count": awaits_count,
            })
    return projects


def scan_transcripts(target_dir, home_dir=None, max_files=40):
    """Scan local agy and Claude Code JSONL transcripts for behavioral metrics & theme hints."""
    home = os.path.abspath(home_dir or os.path.expanduser("~"))
    patterns = [
        os.path.join(home, ".gemini", "antigravity-cli", "brain", "*", ".system_generated", "logs", "transcript.jsonl"),
        os.path.join(home, ".claude", "projects", "*", "*.jsonl"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    files = files[:max_files]

    sessions_scanned = 0
    user_messages = 0
    short_approvals = 0
    tangent_prompts = 0
    total_tool_calls = 0
    user_cancellations = 0
    max_tokens_seen = 0
    french_hits = 0
    english_hits = 0
    dark_mode_Pref = 0
    light_mode_Pref = 0
    color_mentions = []

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                sessions_scanned += 1
                for line in fh:
                    if len(line) > 200000:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    kind = e.get("type") or ""
                    if "input_tokens" in e or "cache_read_tokens" in e:
                        tok = int(e.get("input_tokens") or 0) + int(e.get("cache_read_tokens") or 0)
                        if tok > max_tokens_seen:
                            max_tokens_seen = tok
                    if e.get("terminationReason") == "USER_CANCELED":
                        user_cancellations += 1

                    if kind == "PLANNER_RESPONSE":
                        tcs = e.get("tool_calls") or []
                        total_tool_calls += len(tcs)
                    elif kind == "assistant":
                        msg_c = (e.get("message") or {}).get("content")
                        if isinstance(msg_c, list):
                            total_tool_calls += sum(1 for c in msg_c if isinstance(c, dict) and c.get("type") == "tool_use")

                    user_txt = ""
                    if kind == "USER_INPUT":
                        src = str(e.get("source") or "")
                        if not src or src == "USER_EXPLICIT":
                            user_txt = str(e.get("content") or "")
                            m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", user_txt, re.S)
                            if m:
                                user_txt = m.group(1).strip()
                    elif kind == "user" and not e.get("isMeta"):
                        mc = (e.get("message") or {}).get("content")
                        if isinstance(mc, str):
                            user_txt = mc.strip()
                        elif isinstance(mc, list):
                            txts = [x.get("text") or "" for x in mc if isinstance(x, dict) and x.get("type") == "text"]
                            user_txt = "\n".join(txts).strip()

                    if user_txt and not user_txt.startswith(("<task-notification>", "SYSTEM NOTIFICATION", "Board comment from the owner")):
                        user_messages += 1
                        if APPROVAL_RE.match(user_txt):
                            short_approvals += 1
                        if TANGENT_RE.search(user_txt):
                            tangent_prompts += 1
                        low = user_txt.lower()
                        if any(w in low for w in (" merci ", " bonjour", " c'est ", " avec ", " pour ", " dans ", " est-ce ")):
                            french_hits += 1
                        if any(w in low for w in (" please ", " thanks ", " could you ", " should ", " would ", " what ")):
                            english_hits += 1
                        if "dark mode" in low or "dark theme" in low or "oled" in low:
                            dark_mode_Pref += 1
                        if "light mode" in low or "light theme" in low:
                            light_mode_Pref += 1
                        for hx in HEX_COLOR_RE.findall(user_txt):
                            color_mentions.append("#" + hx.lower())
        except OSError:
            continue

    tools_per_turn = round(total_tool_calls / max(1, user_messages), 2)
    approval_ratio = round(short_approvals / max(1, user_messages), 2)
    tangent_ratio = round(tangent_prompts / max(1, user_messages), 2)
    talk_lang = "French" if french_hits > english_hits and french_hits >= 2 else "English"

    return {
        "sessions_scanned": sessions_scanned,
        "user_messages": user_messages,
        "short_approvals": short_approvals,
        "approval_ratio": approval_ratio,
        "tangent_prompts": tangent_prompts,
        "tangent_ratio": tangent_ratio,
        "total_tool_calls": total_tool_calls,
        "tools_per_user_turn": tools_per_turn,
        "user_cancellations": user_cancellations,
        "max_tokens_seen": max_tokens_seen,
        "inferred_language": talk_lang,
        "dark_mode_mentions": dark_mode_Pref,
        "light_mode_mentions": light_mode_Pref,
        "log_colors": color_mentions[:10],
    }


def extract_project_colors(target_dir, projects=None):
    """Scan CSS, HTML, Tailwind, and config files in target_dir (and sibling projects) for brand hex colors."""
    dirs = [os.path.abspath(target_dir)]
    for p in projects or []:
        if p["path"] not in dirs and os.path.isdir(p["path"]):
            dirs.append(p["path"])

    css_vars = {}
    accent_colors = []
    exts = (".css", ".html", ".scss", ".tsx", ".jsx", ".vue", ".xml", ".json")

    for d in dirs[:5]:
        for root, subdirs, fnames in os.walk(d):
            subdirs[:] = [sd for sd in subdirs if not sd.startswith(".") and sd not in ("node_modules", "__pycache__", "venv", "dist", "build")]
            for fn in fnames:
                if not fn.endswith(exts) or fn in ("package-lock.json", "strings.en.json"):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getsize(fp) > 350000:
                        continue
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        txt = fh.read()
                    for var_name, hex_val in CSS_VAR_RE.findall(txt):
                        css_vars[var_name.lower()] = hex_val.lower()
                    for hx in HEX_COLOR_RE.findall(txt):
                        c = "#" + hx.lower()
                        sat = _hex_saturation(c)
                        lum = _hex_luminance(c)
                        if sat >= 0.35 and 0.12 <= lum <= 0.85 and c not in accent_colors:
                            accent_colors.append(c)
                except OSError:
                    continue

    return css_vars, accent_colors[:12]


def infer_three_palettes(target_dir, projects, transcript_stats):
    """Produce 3 tailored colour palettes from Step 0 inference (repo CSS, log hints, and curated contrasts)."""
    css_vars, accents = extract_project_colors(target_dir, projects)
    log_colors = transcript_stats.get("log_colors") or []
    all_accents = log_colors + accents

    palettes = []

    # Palette 1: Inferred Project Native / Log Preference Palette (if custom accents found, else OLED Cyber or Ember)
    custom_accent = next((c for c in all_accents if c not in ("#b8561c", "#e8894a", "#2f7a4f", "#a33b2b")), None)
    if custom_accent:
        second_accent = next((c for c in all_accents if c != custom_accent), "#6366f1")
        proj_name = os.path.basename(os.path.abspath(target_dir)) or "Project"
        source_note = "chat logs" if custom_accent in log_colors else "repository stylesheets"
        palettes.append({
            "id": "project-inferred",
            "name": "%s Native (%s)" % (proj_name.capitalize(), custom_accent),
            "recommended": True,
            "reason": "Inferred directly from %s (primary accent %s, secondary %s)." % (source_note, custom_accent, second_accent),
            "light": {
                "bg": css_vars.get("bg", "#f3f5f9"),
                "surface": "#ffffff",
                "sunk": "#e5eaf2",
                "ink": "#141b2d",
                "muted": "#556278",
                "line": "#cdd6e5",
                "ember": custom_accent,
                "ember-ink": "#ffffff",
                "good": "#1f7a4c",
                "warn": "#b42318",
                "todo": custom_accent,
                "question": second_accent,
                "info": "#0284c7",
                "done": "#1f7a4c",
            },
            "dark": {
                "bg": "#0b111e",
                "surface": "#141e33",
                "sunk": "#070b14",
                "ink": "#e6edf7",
                "muted": "#94a3b8",
                "line": "#24324d",
                "ember": custom_accent,
                "ember-ink": "#ffffff" if _hex_luminance(custom_accent) < 0.55 else "#0b111e",
                "good": "#34d399",
                "warn": "#f87171",
                "todo": custom_accent,
                "question": second_accent,
                "info": "#38bdf8",
                "done": "#34d399",
            },
        })

    # Add curated contrast options to reach 3 distinct palettes
    prefers_dark = transcript_stats.get("dark_mode_mentions", 0) > transcript_stats.get("light_mode_mentions", 0)
    order = ["oled-cyber", "ember-classic", "forest-sage", "amethyst-night"] if prefers_dark else ["ember-classic", "oled-cyber", "forest-sage", "amethyst-night"]
    for pid in order:
        if len(palettes) >= 3:
            break
        p = json.loads(json.dumps(BUILTIN_PALETTES[pid]))
        p["recommended"] = (len(palettes) == 0)
        palettes.append(p)

    return palettes[:3]


def evaluate_autonomy_and_sprawl(projects, transcript_stats):
    """Evaluate historical handholding vs autonomy and follow-through vs feature sprawl."""
    total_awaits = sum(p.get("awaits_count", 0) for p in projects)
    total_workstreams = sum(len(p.get("workstreams") or []) for p in projects)
    tools_per_turn = transcript_stats.get("tools_per_user_turn", 0.0)
    approval_ratio = transcript_stats.get("approval_ratio", 0.0)
    tangent_ratio = transcript_stats.get("tangent_ratio", 0.0)

    # High sprawl / decision backlog if many awaiting items, many workstreams, or frequent tangents/short approvals
    high_sprawl_or_backlog = (total_awaits >= 4) or (total_workstreams >= 6) or (tangent_ratio >= 0.12)
    high_autonomy_trust = (tools_per_turn >= 3.0) or (approval_ratio >= 0.18) or (transcript_stats.get("sessions_scanned", 0) == 0)

    if high_sprawl_or_backlog and high_autonomy_trust:
        rec_level = "full_auto"
        why = (
            "Measured %d workstreams, %d awaiting decisions, %.1f tool calls/turn, and %d%% short approval/tangent turns. "
            "Enabling Auto-Defaults + Carry-On prevents decision bottlenecks and feature sprawl from stalling active trains."
            % (total_workstreams, total_awaits, tools_per_turn, int((approval_ratio + tangent_ratio) * 100))
        )
    elif high_autonomy_trust:
        rec_level = "balanced"
        why = (
            "Measured %.1f tool calls/turn across %d sessions with manageable backlog (%d awaiting items). "
            "Auto-Consultant reviews route-setting milestones while waiting for your 1-tap 👍 on new workstreams."
            % (tools_per_turn, transcript_stats.get("sessions_scanned", 0), total_awaits)
        )
    else:
        rec_level = "gated"
        why = "Low tool-per-turn ratio or frequent manual steering detected; step-by-step approval gates recommended."

    profiles = [
        {
            "id": "full_auto",
            "name": "Continuous Autonomous + Auto-Defaults",
            "recommended": rec_level == "full_auto",
            "summary": "Auto-spawn Consultant, act on continue/redirect automatically, apply default_option & carry on when questions wait, park side-tangents into memory.",
            "config": {
                "level": "full_auto",
                "auto_consultant": True,
                "auto_defaults": True,
                "carry_on": True,
                "park_tangents": True,
            },
        },
        {
            "id": "balanced",
            "name": "Balanced Orchestration (1-Tap 👍 + Consultant)",
            "recommended": rec_level == "balanced",
            "summary": "Auto-spawn Consultant at route-setting Done; launch parallel parts on a 1-tap 👍; wait on question cards.",
            "config": {
                "level": "balanced",
                "auto_consultant": True,
                "auto_defaults": False,
                "carry_on": False,
                "park_tangents": True,
            },
        },
        {
            "id": "gated",
            "name": "Hands-On Gated Mode",
            "recommended": rec_level == "gated",
            "summary": "Require explicit owner confirmation before launching parts, applying choices, or merging branches.",
            "config": {
                "level": "gated",
                "auto_consultant": False,
                "auto_defaults": False,
                "carry_on": False,
                "park_tangents": False,
            },
        },
    ]

    return {
        "recommended_level": rec_level,
        "reason": why,
        "total_workstreams": total_workstreams,
        "total_awaits": total_awaits,
        "profiles": profiles,
    }


CLIS = ("agy", "claude")


def installed_clis():
    """The agent CLIs on this computer's PATH (agy first)."""
    import shutil
    return [c for c in CLIS if shutil.which(c)]


def running_cli():
    """The CLI this process runs under: `claude` (the local Claude Code CLI, not Claude Code Web) or `agy`, else None.
    Claude Code sets CLAUDECODE in its tools' environment; otherwise walk the parent processes by name (ps, any Unix)."""
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return None
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude"
    import subprocess
    pid = os.getppid()
    for _ in range(12):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout.split(None, 1)
        except (OSError, subprocess.SubprocessError):
            return None
        if len(out) < 2:
            return None
        name = os.path.basename(out[1].strip()).lower()
        for c in CLIS:
            if name == c or name.startswith(c + "-") or name.startswith(c + "."):
                return c
        pid = int(out[0])
        if pid <= 1:
            return None
    return None


def pick_cli(installed=None, running=None):
    """The CLI the one-word launcher opens when the owner named none: the one running the install, else the only
    one installed, else agy if installed, else claude, else agy."""
    installed = installed_clis() if installed is None else installed
    running = running_cli() if running is None else running
    if running:
        return running
    if installed:
        return installed[0]
    return "agy"


def detect_current_runtime():
    if os.environ.get("KIT_EMBED_PREVIEW") or os.path.exists("/home/user"):
        return "arena"
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return "claude"
    if running_cli() or installed_clis() or os.path.isdir(os.path.expanduser("~/.gemini/antigravity-cli")):
        return "local"
    return "generic"


def device_survey(save=False):
    """CL15: this device's session cap (.kit/device.py, scripts/device.py in the kit's repo); None if missing."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("kit_device", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        s = mod.survey()
        if save:
            st = mod.load_store()
            st["survey"] = s
            mod.save_store(st)
        s["summary"] = mod.summary(s)
        return s
    except Exception as e:  # a probe never breaks step 0
        return {"error": str(e)}


# CL22: permissions the repo's own instructions already give (install.py and the wizard read them here).
GRANT_FILES = ("CLAUDE.md", "AGENTS.md", os.path.join(".claude", "CLAUDE.md"), "GEMINI.md")
PERMISSIVE_MODES = ("auto", "acceptEdits", "bypassPermissions", "dontAsk")
_NEG = re.compile(r"\b(never|not|don'?t|doesn'?t|isn'?t|cannot|can'?t|forbidden|refuse[sd]?|no permission)\b", re.I)
_PUSH = re.compile(r"\bpush(es|ing|ed)?\b", re.I)
_PERM = re.compile(r"\b(permission|permitted|allowed|allow|may|ok|okay|authori[sz]ed|fine|free to)\b", re.I)


def detect_trunk_push(t, trunk_name=None):
    """(file, line no, line) of the first line in the repo's own instructions granting pushes to the trunk, else None."""
    names = {"main", "master", "trunk"} | ({trunk_name} if trunk_name else set())
    trunk_re = re.compile(r"(?<![\w/-])`?(%s)`?(?![\w/-])" % "|".join(re.escape(n) for n in sorted(names)), re.I)
    for rel in GRANT_FILES:
        fp = os.path.join(t, rel)
        if not os.path.isfile(fp):
            continue
        for i, line in enumerate(open(fp, encoding="utf-8", errors="replace").read().splitlines(), 1):
            for sent in re.split(r"(?<=[.!?])\s+", line):
                if _PUSH.search(sent) and trunk_re.search(sent) and _PERM.search(sent) and not _NEG.search(sent):
                    return rel, i, line.strip()
    return None


def detect_permission_mode(t):
    """(file, mode) when the repo's .claude settings already set a permissive defaultMode, else None."""
    for rel in (os.path.join(".claude", "settings.local.json"), os.path.join(".claude", "settings.json")):
        fp = os.path.join(t, rel)
        try:
            mode = (json.load(open(fp, encoding="utf-8")).get("permissions") or {}).get("defaultMode")
        except (OSError, ValueError, AttributeError):
            continue
        if mode in PERMISSIVE_MODES:
            return rel, mode
    return None


def build_step0_profile(target_dir, home_dir=None):
    target = os.path.abspath(target_dir)
    projects = discover_projects(target, home_dir=home_dir)
    t_stats = scan_transcripts(target, home_dir=home_dir)
    palettes = infer_three_palettes(target, projects, t_stats)
    autonomy = evaluate_autonomy_and_sprawl(projects, t_stats)
    detected_rt = detect_current_runtime()

    multi_project = len(projects) > 1
    scope_options = [
        {
            "id": "single",
            "name": "This Project Only (%s)" % (os.path.basename(target) or "repo"),
            "recommended": not multi_project or any(p["is_target"] and len(p["workstreams"]) > 0 for p in projects),
            "projects": [os.path.basename(target) or "repo"],
        },
        {
            "id": "all",
            "name": "All Discovered Projects (%d repos)" % len(projects),
            "recommended": multi_project and not any(p["is_target"] and len(p["workstreams"]) > 0 for p in projects),
            "projects": [p["name"] for p in projects],
        },
    ]

    clis_found = installed_clis()
    cli_now = running_cli()
    cli_pick = pick_cli(clis_found, cli_now)
    cli_options = [
        {"id": c, "name": {"agy": "Antigravity CLI (agy --mode auto)", "claude": "Claude Code CLI (claude --permission-mode auto)"}[c]
                          + ("" if c in clis_found else " — not installed"),
         "recommended": c == cli_pick, "installed": c in clis_found}
        for c in CLIS
    ]
    runtime_options = [
        {"id": "local", "name": "This computer: agy AND/OR Claude Code CLI (local board + Tailscale + hooks for both)", "recommended": detected_rt == "local"},
        {"id": "agy", "name": "Antigravity CLI only (agy — local board + Tailscale + hooks)", "recommended": detected_rt == "agy"},
        {"id": "claude", "name": "Claude Code Web (claude.ai Artifact + cloud sessions)", "recommended": detected_rt == "claude"},
        {"id": "arena", "name": "LMArena / E2B Sandbox (--preview --embed-mode + 30m listener)", "recommended": detected_rt == "arena"},
        {"id": "both", "name": "Hybrid (agy local + Claude Code Web over Git mirror)", "recommended": False},
        {"id": "generic", "name": "Generic CLI / Cursor / Codex (local server + AGENTS.md)", "recommended": detected_rt == "generic"},
    ]

    mcp_info = detect_mcp_readers(target)
    grant = detect_trunk_push(target)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_dir": target,
        "project_name": os.path.basename(target) or "project",
        "inferred_language": t_stats["inferred_language"],
        "detected_runtime": detected_rt,
        "trunk_push_grant": ("%s:%d" % grant[:2]) if grant else None,
        "discovered_projects": projects,
        "scope_options": scope_options,
        "transcript_metrics": t_stats,
        "autonomy_assessment": autonomy,
        "palettes": palettes,
        "runtime_options": runtime_options,
        "cli_options": cli_options,
        "installed_clis": clis_found,
        "running_cli": cli_now,
        "mcp_readers": mcp_info["readers"],
        "mcp_reader_details": mcp_info["details"],
        "mcp_capabilities": mcp_info["capabilities"],
        "device": device_survey(),
    }


MCP_READER_RULES = [
    ("sharepoint", "Microsoft 365", ["sharepoint_search", "read_resource"],
     [r"sharepoint\.com/", r"onedrive\.live\.com/"], ["microsoft 365", "microsoft365", "m365", "sharepoint"]),
    ("outlook", "Microsoft 365", ["outlook_email_search", "read_resource"],
     [r"outlook\.(?:office|office365|live)\.com/mail/"], ["outlook"]),
    ("gmail", "Gmail", ["gmail_read_thread", "gmail_get_message"],
     [r"mail\.google\.com/mail/"], ["gmail"]),
    ("gdrive", "Google Drive", ["google_drive_fetch"],
     [r"(?:docs|drive)\.google\.com/(?:document|spreadsheets|presentation|file)/d/"], ["google drive", "gdrive"]),
    ("notion", "Notion", ["notion-fetch"],
     [r"notion\.(?:so|site)/"], ["notion"]),
    ("linear", "Linear", ["get_issue"],
     [r"linear\.app/[^/\s]+/issue/"], ["linear"]),
]


def detect_mcp_readers(target_dir):
    """Scan repo docs, memory, and MCP/Claude configs to detect which MCP Link Reader adapters are needed."""
    target = os.path.abspath(target_dir)
    found = {}
    url_files = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".kit")]
        for fn in files:
            if fn.endswith((".md", ".txt", ".json")) and len(url_files) < 120:
                url_files.append(os.path.join(root, fn))
    for fp in url_files:
        try:
            if os.path.getsize(fp) > 262144:
                continue
            txt = open(fp, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        is_cfg = os.path.basename(fp) in (".mcp.json", "settings.json", "settings.local.json")
        low = txt.lower()
        for rid, srv, _tools, url_pats, cfg_keys in MCP_READER_RULES:
            if rid in found:
                continue
            if any(re.search(pat, txt, re.I) for pat in url_pats):
                found[rid] = {"id": rid, "server": srv, "reason": "URL found in " + os.path.relpath(fp, target)}
            elif is_cfg and any(k in low for k in cfg_keys):
                found[rid] = {"id": rid, "server": srv, "reason": "connector referenced in " + os.path.relpath(fp, target)}
    ordered = [r[0] for r in MCP_READER_RULES if r[0] in found]
    by_server = {}
    srv_order = []
    for rid, srv, tools, _, _ in MCP_READER_RULES:
        if rid not in found:
            continue
        if srv not in by_server:
            by_server[srv] = []
            srv_order.append(srv)
        for t in tools:
            if t not in by_server[srv]:
                by_server[srv].append(t)
    return {
        "readers": ordered,
        "details": [found[rid] for rid in ordered],
        "capabilities": {"servers": [{"server": s, "tools": by_server[s]} for s in srv_order]},
    }


def render_markdown_report(profile):
    tm = profile["transcript_metrics"]
    au = profile["autonomy_assessment"]
    projs = profile["discovered_projects"]
    lines = [
        "# Step 0 Survey & Behavioral Profile — %s" % profile["project_name"],
        "",
        "*Generated at %s by `scripts/step0_scan.py`*" % profile["generated_at"],
        "",
        "## 1. Discovered Projects & Scope",
        "- **Target repository:** `%s`" % profile["target_dir"],
        "- **Detected projects (%d):** %s" % (len(projs), ", ".join("`%s` (%d workstreams, %d waiting)" % (p["name"], len(p["workstreams"]), p["awaits_count"]) for p in projs)),
        "- **Inferred owner language:** `%s`" % profile["inferred_language"],
        "- **Detected runtime environment:** `%s`" % profile["detected_runtime"],
        "",
        "## 2. Historical Autonomy, Follow-Through & Sprawl Metrics",
        "| Metric | Measured Value | Interpretation |",
        "|---|---|---|",
        "| **Sessions scanned** | %d | Local `agy` / `.claude` JSONL transcripts |" % tm["sessions_scanned"],
        "| **Tool calls per user turn** | %.2f | %s |" % (tm["tools_per_user_turn"], "High agent autonomy" if tm["tools_per_user_turn"] >= 3.0 else "Frequent user turn-taking"),
        "| **Short approval ratio** (`yes`/`continue`/`ok`) | %d%% (%d/%d) | %s |" % (int(tm["approval_ratio"] * 100), tm["short_approvals"], tm["user_messages"], "Handholding friction can be automated" if tm["approval_ratio"] >= 0.15 else "Low approval overhead"),
        "| **Feature-sprawl / tangent rate** | %d%% (%d/%d) | %s |" % (int(tm["tangent_ratio"] * 100), tm["tangent_prompts"], tm["user_messages"], "Recommend parking tangents & auto-defaults" if tm["tangent_ratio"] >= 0.12 else "Focused linear follow-through"),
        "| **Open workstreams & awaiting decisions** | %d workstreams / %d waiting | %s |" % (au["total_workstreams"], au["total_awaits"], "Auto-defaults carry-on recommended" if au["total_awaits"] >= 4 else "Manageable decision queue"),
        "",
        "**Recommended Autonomy Profile:** **`%s`** — %s" % (au["recommended_level"], au["reason"]),
        "",
        "## 3. Inferred Colour Palettes (3 Options)",
    ]
    for idx, pal in enumerate(profile["palettes"], 1):
        badge = " *(★ Recommended)*" if pal.get("recommended") else ""
        lines.append("%d. **`%s` — %s**%s" % (idx, pal["id"], pal["name"], badge))
        lines.append("   - *Why:* %s" % pal["reason"])
        lines.append("   - *Light:* `bg %s` · `ember %s` · `question %s` · `done %s`" % (
            pal["light"]["bg"], pal["light"]["ember"], pal["light"]["question"], pal["light"]["done"]))
        lines.append("   - *Dark:* `bg %s` · `ember %s` · `question %s` · `done %s`" % (
            pal["dark"]["bg"], pal["dark"]["ember"], pal["dark"]["question"], pal["dark"]["done"]))
    mcp_details = profile.get("mcp_reader_details") or []
    if mcp_details:
        lines.append("")
        lines.append("## 4. Detected MCP Link Readers")
        for d in mcp_details:
            lines.append("- **`%s`** (`%s`): %s" % (d["id"], d["server"], d["reason"]))
        lines.append("- **Artifact `capabilities.mcp`:** `%s`" % json.dumps(profile.get("mcp_capabilities") or {"servers": []}))
    dev = profile.get("device") or {}
    if dev.get("summary"):
        lines.append("")
        lines.append("## 5. This device (session cap)")
        lines.append("- %s" % dev["summary"])
        lines.append("- Cores %s · swap %s MB · GPUs: %s (information only: sessions use the API, RAM bounds them)." % (
            dev.get("cores"), dev.get("swap_total_mb"), ", ".join(dev.get("gpus") or []) or "none found"))
        lines.append("- Launches past the cap wait their turn (`.kit/device.py --gate`, the guard). Override on this "
                     "machine: `python3 -I .kit/device.py --set-cap N`.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Step 0 cross-platform scanner & profiler")
    ap.add_argument("target", nargs="?", default=".", help="Target repository directory")
    ap.add_argument("--home", default=None, help="Override home directory for transcript/project discovery")
    ap.add_argument("--out-json", default=None, help="Write structured Step 0 profile JSON to path")
    ap.add_argument("--out-report", default=None, help="Write Step 0 Markdown report to path")
    a = ap.parse_args(argv)

    profile = build_step0_profile(a.target, home_dir=a.home)
    device_survey(save=True)  # the cap is the machine's: ~/.config/board-kit/device.json, never the repo
    if a.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_json)), exist_ok=True)
        with open(a.out_json, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    if a.out_report:
        os.makedirs(os.path.dirname(os.path.abspath(a.out_report)), exist_ok=True)
        with open(a.out_report, "w", encoding="utf-8") as fh:
            fh.write(render_markdown_report(profile))
    print(json.dumps(profile, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
