"""The Pydantic model used by ``messages.parse``.

Isolated behind a function because pydantic ships with the Anthropic SDK: where
the SDK is absent so is pydantic, and importing it at module scope would break
the optional-dependency contract the rest of the package relies on.
"""

from __future__ import annotations

from typing import Any, Optional

from .schema import EVENT_CLASSES, HOLDER_TYPES

_CACHED: Any = None
_TRIED = False


def llm_event_model() -> Optional[Any]:
    """Build (once) the Pydantic model mirroring EVENT_JSON_SCHEMA, or None."""
    global _CACHED, _TRIED
    if _TRIED:
        return _CACHED
    _TRIED = True
    try:
        from typing import Literal

        from pydantic import BaseModel, Field
    except ImportError:  # pragma: no cover - no SDK, no pydantic
        _CACHED = None
        return None

    class LlmEvent(BaseModel):
        model_config = {"extra": "forbid"}

        event_class: Literal[EVENT_CLASSES]  # type: ignore[valid-type]
        holder: Optional[str] = None
        holder_type: Literal[HOLDER_TYPES]  # type: ignore[valid-type]
        stake_pct: Optional[float] = None
        effective_date: Optional[str] = None
        confidence: float = Field(ge=0.0, le=1.0)

    _CACHED = LlmEvent
    return _CACHED
