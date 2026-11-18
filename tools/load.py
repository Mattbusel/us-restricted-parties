#!/usr/bin/env python3
"""Kept for scripts that run ``python tools/load.py`` or ``from load import load``.

The loader is the ``us_restricted_parties`` package (``src/us_restricted_parties/__init__.py``, one
standard-library file). Install it with pip, or copy that file into your project as
``us_restricted_parties.py``. This shim uses the installed package, or the copy in this repository.
"""
import sys
from pathlib import Path

try:
    import us_restricted_parties as _impl
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    import us_restricted_parties as _impl

if __name__ == "__main__":
    sys.exit(_impl.main())
sys.modules[__name__] = _impl
