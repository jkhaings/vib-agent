"""Runtime read path for the operator-approved cause layer (knowledge/causes.yaml).

Session R2-BUILD. See loader.py for why this package exists at all rather than
the product importing the repo-root `knowledge/` package directly.
"""

from vib_agent.knowledge.loader import (
    BEARING_FAULT_FAMILIES,
    CAUSE_HEADING,
    Cause,
    CauseBook,
    CauseCitation,
    CauseObservation,
    load_causes,
    lookup_causes,
    lookup_causes_for,
)

__all__ = [
    "BEARING_FAULT_FAMILIES",
    "CAUSE_HEADING",
    "Cause",
    "CauseBook",
    "CauseCitation",
    "CauseObservation",
    "load_causes",
    "lookup_causes",
    "lookup_causes_for",
]
