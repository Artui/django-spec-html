from __future__ import annotations

from enum import Enum


class Outcome(str, Enum):
    """How a post to a page ended, decided once and then wrapped for whoever asked."""

    OK = "ok"
    """Dispatch returned a redirect, or a fragment answering ``HX-Redirect`` or ``spec-result``."""

    INVALID = "invalid"
    """The arguments were refused: the form re-rendered with the refusal placed on its fields."""

    NEEDS_INPUT = "needs_input"
    """The service asked for one more value, through ``AdditionalInputRequired``."""

    DENIED = "denied"
    """The principal may not, or the target is not there: ``PermissionDenied`` or ``Http404``."""

    FAILED = "failed"
    """The operation refused itself: its message, among the form's non-field errors."""
