"""``HTML_TRANSPORT`` - the name this package stamps on the call context of every call it makes."""

from __future__ import annotations

from typing import Final

HTML_TRANSPORT: Final = "html"
"""What a callable reads under the kernel's ``TRANSPORT_KEY`` when this package made the call.

A plain string, not a member of a kernel type: the kernel names no transport,
and each package owns and exports its own value. A callable compares it as
``call_transport(call_context) == HTML_TRANSPORT``, or to the literal
``"html"``; both read one value. It covers the JSON views, the pages and the
admin's offer of an action, since all of them build their context through
``html_call_context``."""
