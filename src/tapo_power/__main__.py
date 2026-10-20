"""Entry point for `python -m tapo_power` and the standalone executable build."""

import sys

# Absolute import: PyInstaller runs this file as a script, not as a package module.
from tapo_power.cli import main

if __name__ == "__main__":
    sys.exit(main())
