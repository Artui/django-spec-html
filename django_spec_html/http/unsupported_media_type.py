from __future__ import annotations

from typing import ClassVar

from django_service_specs import DispatchError


def gettext_noop(message: str) -> str:
    """Mark ``message`` as a msgid and return it unchanged."""
    return message


class UnsupportedMediaType(DispatchError):
    """A request carried a body that is neither JSON nor a form."""

    default_message: ClassVar[str] = gettext_noop(
        "The request body is in a format this transport does not read."
    )
