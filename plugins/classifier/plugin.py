"""Classifier plugin: thin adapter over the core routing rules.

Keeping the rules in `app.classifier` avoids the drift that happened when the
same regex list lived in two places. This plugin only exposes the core rule
function through the `classify.prompt` hook (or a future LLM router can replace
it without touching the rest of the kernel).
"""
from __future__ import annotations

from typing import Any

from app.kernel.api import PluginContext, classify_rules


def _on_classify(**kwargs: Any) -> str:
    return classify_rules(kwargs.get("text", ""))


def setup(ctx: PluginContext) -> None:
    ctx.hooks.on("classify.prompt", _on_classify)


def teardown(ctx: PluginContext) -> None:
    pass
