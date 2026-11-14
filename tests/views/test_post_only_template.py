from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django_service_specs import Affordance

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from tests.dispatch.utils import make_user
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in
from tests.views.test_default_templates import WITHOUT_THE_APP
from tests.views.test_spec_confirm_view import PRESSED, archive_spec

pytestmark = pytest.mark.django_db

PAUSED = Affordance(code="paused", reason="Archiving is paused.", when=lambda: False)
POST_ONLY = {"http_method_names": ["post"], "refusal_redirect": True}


def build(**initkwargs: Any) -> Any:
    return SpecConfirmView.as_view(
        spec=archive_spec(affordances=[PAUSED]),
        route_kwargs=("pk",),
        success_url="/done/",
        **initkwargs,
    )


class TestAPostOnlyRedirectingPage:
    def test_is_built_without_the_shipped_templates(self) -> None:
        with override_settings(INSTALLED_APPS=WITHOUT_THE_APP):
            assert build(**POST_ONLY)

    @pytest.mark.parametrize(
        "initkwargs",
        [
            {"http_method_names": ["get", "post"], "refusal_redirect": True},
            {"http_method_names": ["head", "post"], "refusal_redirect": True},
            {"http_method_names": ["post"], "refusal_redirect": False},
        ],
    )
    def test_a_page_that_renders_is_still_checked(self, initkwargs: dict[str, Any]) -> None:
        with (
            override_settings(INSTALLED_APPS=WITHOUT_THE_APP),
            pytest.raises(ImproperlyConfigured, match="spec_confirm.html"),
        ):
            build(**initkwargs)

    def test_the_class_attributes_count_as_initkwargs_do(self) -> None:
        class Only(SpecConfirmView):
            http_method_names = ["post"]
            refusal_redirect = True

        with override_settings(INSTALLED_APPS=WITHOUT_THE_APP):
            assert Only.as_view(spec=archive_spec(), route_kwargs=("pk",), success_url="/done/")

    def test_a_refusal_with_no_messages_answers_its_status_with_an_empty_body(self) -> None:
        ada = make_user("ada")
        note = Note.objects.create(owner=ada, title="Buy milk")
        request = signed_in(FACTORY.post("/", data=PRESSED), ada)
        with override_settings(INSTALLED_APPS=WITHOUT_THE_APP):
            response = build(**POST_ONLY)(request, pk=note.pk)
        assert (response.status_code, response.content) == (409, b"")

    def test_a_refusal_with_the_page_found_still_renders_it(self) -> None:
        ada = make_user("ada")
        note = Note.objects.create(owner=ada, title="Buy milk")
        request = signed_in(FACTORY.post("/", data=PRESSED), ada)
        response = build(**POST_ONLY)(request, pk=note.pk)
        assert (response.status_code, bool(response.content)) == (409, True)
