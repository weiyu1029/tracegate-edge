"""TraceGate Edge reference implementation.

The package intentionally uses only the Python standard library so it can run
inside the Python development container already shipped with a Portenta X8.
All identifiers and production data in this repository are synthetic.
"""

__version__ = "0.2.0"

from .config import Settings
from .db import Database
from .service import TraceGateService

__all__ = ["Database", "Settings", "TraceGateService"]
