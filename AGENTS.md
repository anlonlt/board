# Board Kit Repository — Agent Instructions

When making any change to `kit/`, `scripts/`, `install.py`, or `tests/` in this repository:

0. **Read `docs/DESIGN-PRINCIPLES.md` first** before any UI change (mobile first, gestures before buttons, icon first with zero emoji, progressive disclosure with fade, pointer-aware drawers with touch gestures, scroll containment, OS-style windows, an interface that learns per device, two layers in every post, cost rules: a Like costs a yes / say it once / do it don't ask, editorial Markdown, 5-state colors, zero dependencies).
1. Run the unified regression suite before committing:
   ```bash
   python3 scripts/run_all_tests.py
   ```
   Confirm `0 failed` across all suites (`tests/`, `kit/board/test_board_local.py`, `kit/board/test_strings.py`, `kit/board/test_kit_cards.py`, `test_install.py`, `test_install_bd11.py`, `kit/board/test_page_layout.js`: no text runs off screen or is cut, at a phone and a desktop width; `kit/board/test_page_speed.js`: a deep thread opens without freezing).
2. Never overwrite a non-empty `_dashboard` with empty or placeholder rows (`kit/board/kit_cards.py` enforces this safety guard unless `--force` is passed).
