"""``Outcome`` - the five ways a page's post ends."""

from __future__ import annotations

from enum import Enum


class Outcome(str, Enum):
    """How a post to a page ended, decided once and then wrapped for whoever asked.

    Read from ``REFUSAL_STATUSES``' third column for what dispatch raised, or
    ``OK`` for what it returned. The page and the fragment answer the same
    outcome, each in its own way: the page at the row's status, the fragment
    at 200, and a denial in neither, raised for the host's 403 and 404 pages.

    A ``str`` enum, so an outcome compares equal to its value. Compare
    members, or read ``.value``: ``str()`` spells the member, ``Outcome.OK``,
    on every Python, and an f-string or ``format()`` spells its value only
    before Python 3.11 (held by ``test_its_spelling_is_not_its_value``).
    """

    OK = "ok"
    """Dispatch returned: the page redirects, and the fragment answers ``HX-Redirect`` or ``spec-result``."""

    INVALID = "invalid"
    """The arguments were refused: the form re-rendered with the refusal placed on its fields."""

    NEEDS_INPUT = "needs_input"
    """The service asked for one more value, through ``AdditionalInputRequired``."""

    DENIED = "denied"
    """The principal may not, or the target is not there: ``PermissionDenied`` or ``Http404``."""

    FAILED = "failed"
    """The operation refused itself: its message, among the form's non-field errors."""
