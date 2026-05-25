"""Run the Tabuflow CLI package with ``python -m tabuflow.cli``."""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
