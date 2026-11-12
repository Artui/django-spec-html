from __future__ import annotations

from typing import ClassVar

from django_service_specs import DispatchError


def gettext_noop(message: str) -> str:
    """Mark ``message`` as a msgid and return it unchanged."""
    return message


class UnsupportedMediaType(DispatchError):
    """A request carried a body that is neither JSON nor a form.

    Refused rather than read as no arguments. Read as none, a PATCH sent as
    ``text/plain`` or ``application/merge-patch+json`` to an operation whose
    parameters are all optional would run with nothing and answer success,
    which tells its client the change was made. A request with no body is not
    refused, whatever it is labelled, since there is nothing in it to misread.

    ``default_message`` is a msgid in the package's catalog, looked up in the
    active language each time a refusal is built with no message of its own. A
    ``message`` the caller passes is used as given.

    A refusal of the call rather than the operation's verdict, so a
    ``DispatchError``, answered 415 as djangorestframework-services answers it.
    """

    default_message: ClassVar[str] = gettext_noop(
        "The request body is in a format this transport does not read."
    )
