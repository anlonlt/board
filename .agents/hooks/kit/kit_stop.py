#!/usr/bin/env python3
"""Moved to .kit/hooks/ (install.py --unified-hooks): this forwards, for sessions started before the move."""
import os, runpy
p = os.path.abspath(__file__)
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(p))))
runpy.run_path(os.path.join(root, ".kit", "hooks", os.path.basename(p)), run_name="__main__")
