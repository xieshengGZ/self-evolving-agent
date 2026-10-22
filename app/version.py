from __future__ import annotations

from pathlib import Path

VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
MAJOR, MINOR, PATCH = (int(part) for part in VERSION.split("."))
CHANNEL = "development" if MINOR % 2 else "stable"
