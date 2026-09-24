from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from jevfuse.engine import JevFuseEngine
from jevfuse.schema.decision import (
    Action,
    DecisionKind,
    DecisionRequest,
    DecisionResponse,
)
from jevfuse.schema.errors import JevFuseError

__version__ = "0.1.0"

__all__ = [
    "Action",
    "DecisionKind",
    "DecisionRequest",
    "DecisionResponse",
    "JevFuseEngine",
    "JevFuseError",
    "__version__",
]

