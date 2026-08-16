"""Shared fixtures for backend tests."""

import sys
from pathlib import Path

# Ensure backend package is importable regardless of working directory.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
