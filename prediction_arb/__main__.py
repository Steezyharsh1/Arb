"""Enable ``python -m prediction_arb`` as an alias for
``python -m prediction_arb.main``."""
from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
