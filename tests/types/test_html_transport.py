from __future__ import annotations

import django_spec_html
from django_spec_html import HTML_TRANSPORT


def test_the_value_is_the_plain_string_html() -> None:
    # A bare str, not an enum member: a consumer compares the stamp to a string.
    assert type(HTML_TRANSPORT) is str
    assert HTML_TRANSPORT == "html"


def test_the_root_exports_it() -> None:
    assert "HTML_TRANSPORT" in django_spec_html.__all__
