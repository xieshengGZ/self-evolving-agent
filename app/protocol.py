"""Typed envelope for the model action protocol.

Plugins still own their action names and arguments, so the envelope validates
the stable part (`action` is a non-empty string) and preserves extra fields for
the runtime/tool layer.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    action: str = Field(min_length=1, max_length=64)


def validate_action(value: Any) -> Tuple[Optional[dict], str]:
    """Return (normalized_action, error)."""
    if not isinstance(value, dict):
        return None, "动作必须是 JSON 对象"
    try:
        model = ActionEnvelope.model_validate(value)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        message = str(first.get("msg") or "格式不合法")
        return None, f"动作格式不合法: {message}"
    return model.model_dump(exclude_none=True), ""
