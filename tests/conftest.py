import sys
from pathlib import Path

# Ensure the package directory (project package root) is on sys.path for tests
# This file lives in <repo>/sci-cluster/tests/conftest.py; the package root we want is its parent (..)
pkg_root = Path(__file__).resolve().parents[1]
if str(pkg_root) not in sys.path:
    sys.path.insert(0, str(pkg_root))
