# Board Kit Repository — Agent Instructions

When making any change to `kit/`, `scripts/`, `install.py`, or `tests/` in this repository:

0. **Read `docs/DESIGN-PRINCIPLES.md` first** before any UI change (zero emojis, icon-first chrome, pointer-aware drawers, scroll containment, OS-style movable/resizable/snappable windows, editorial Markdown typography, 5-state colors, zero dependencies).
1. Run the unified regression suite before committing:
   ```bash
   python3 scripts/run_all_tests.py
   ```
   Confirm `0 failed` across all suites (`tests/`, `kit/board/test_board_local.py`, `kit/board/test_strings.py`, `kit/board/test_kit_cards.py`, `test_install.py`, `test_install_bd11.py`).
2. Never overwrite a non-empty `_dashboard` with empty or placeholder rows (`kit/board/kit_cards.py` enforces this safety guard unless `--force` is passed).
