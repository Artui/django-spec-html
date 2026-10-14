"""Helpers the parameters tests share, and the tests of the transports that refuse arguments too."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def coded(detail: Any) -> Any:
    """``detail`` with every message read as ``(message, code)``, ``None`` for a message with none.

    Read through ``getattr`` rather than the message's type, so a test written
    with it compiles against a tree that carries no codes at all, and fails
    there by its assertion rather than at import. The pair, not the code alone,
    so each assertion also says which message answered.
    """
    if isinstance(detail, Mapping):
        return {key: coded(value) for key, value in detail.items()}
    if isinstance(detail, list):
        return [coded(message) for message in detail]
    return (str(detail), getattr(detail, "code", None))
