from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from docs.examples.htmx_target import AddBookByTarget

pytestmark = pytest.mark.django_db

FACTORY = RequestFactory()


def served(**headers: str) -> Any:
    request = FACTORY.get("/books/new/", **headers)
    request.user = get_user_model().objects.create_user(username="ada")
    return AddBookByTarget.as_view()(request).render()


def test_a_request_for_the_dialog_is_the_dialog_fragment() -> None:
    response = served(HTTP_HX_REQUEST="true", HTTP_HX_TARGET="dialog")
    assert response.content.startswith(b"<dialog open>")


def test_a_request_for_any_other_target_is_the_shipped_fragment() -> None:
    response = served(HTTP_HX_REQUEST="true", HTTP_HX_TARGET="panel")
    assert response.content.lstrip().startswith(b"<form")
    assert b"<dialog" not in response.content


def test_a_target_header_without_htmx_is_the_page() -> None:
    assert b"<dialog" not in served(HTTP_HX_TARGET="dialog").content
