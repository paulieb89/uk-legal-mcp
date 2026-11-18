"""Entry point for `fastmcp inspect`, which loads a file rather than a package.

src/gateway.py uses package-relative imports, so it cannot be inspected
directly. This file puts the repo root on sys.path and re-exports the gateway:

    fastmcp inspect scripts/inspect_entry.py:gateway --format fastmcp -o manifest.json
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gateway import gateway  # noqa: E402,F401
