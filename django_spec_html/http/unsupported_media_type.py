"""``UnsupportedMediaType`` - a request body in a format the HTTP transport does not read."""

from __future__ import annotations

from typing import ClassVar

from django_service_specs import DispatchError


def gettext_noop(message: str) -> str:
    """Mark ``message`` as a msgid and return it unchanged.

    Not ``django.utils.translation.gettext_noop``: that one reads ``USE_I18N``
    each time it is called, and a refusal's ``default_message`` is marked when
    its class is defined, so with it this module could not be imported before a
    project's settings are configured
    (test_marking_a_msgid_reads_no_setting). Named exactly ``gettext_noop``
    because ``makemessages`` passes ``--keyword=gettext_noop`` to xgettext,
    which matches a call by its name and never by where it was imported from; a
    private name would leave the msgid out of the catalog
    (test_every_default_message_is_marked_for_extraction). The kernel marks its
    own the same way, but does not export the marker.
    """
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
