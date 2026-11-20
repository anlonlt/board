"""The kit's config, read by every hook: $CLAUDE_PROJECT_DIR/.kit/config.json (else the current directory's)."""
import json
import os


def project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def config():
    p = os.path.join(project_dir(), ".kit", "config.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def limit(name, default):
    return int(config().get("kit", {}).get("limits", {}).get(name, default))
