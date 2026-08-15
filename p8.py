#!/usr/bin/env python3
"""Entry point that works without installing anything."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p8tool.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
