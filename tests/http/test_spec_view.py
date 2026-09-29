"""``SpecView``: a spec served as a class-based view, methods and all."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt

from django_service_specs.http.spec_view import SpecView
from django_service_specs.validation.unknown_arguments import UnknownArguments
from tests.dispatch.utils import TENANT_SEEDS, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, body, note_spec, notes_spec, signed_in, titled_spec

pytestmark = pytest.mark.django_db

NO_SPEC = "SpecView.as_view() needs a spec: a ServiceSpec or a SelectorSpec, got {got}."


@pytest.fixture
def ada() -> Any:
    user = make_user("ada")
    Note.objects.create(owner=user, title="One")
    return user


def send(view: Any, method: str, user: Any, payload: Any = None, **url_kwargs: Any) -> Any:
    data = json.dumps(payload) if payload is not None else b""
    request = FACTORY.generic(method, "/", data=data, content_type="application/json")
    return view(signed_in(request, user), **url_kwargs)


class TestMethods:
    @pytest.mark.parametrize("method", ["GET", "HEAD"])
    def test_a_selector_answers_get_and_head(self, ada: Any, method: str) -> None:
        response = send(SpecView.as_view(spec=notes_spec()), method, ada)
        assert (response.status_code, body(response)) == (200, [{"title": "One"}])

    def test_a_selector_refuses_post_with_djangos_405(self, ada: Any) -> None:
        response = send(SpecView.as_view(spec=notes_spec()), "POST", ada)
        assert response.status_code == 405
        assert response["Allow"] == "GET, HEAD, OPTIONS"

    def test_a_service_answers_post(self, ada: Any) -> None:
        response = send(SpecView.as_view(spec=titled_spec({"id": 1})), "POST", ada, {"title": "x"})
        assert (response.status_code, body(response)) == (200, {"id": 1})

    @pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "PATCH", "DELETE", "TRACE"])
    def test_a_service_answers_nothing_but_post(self, ada: Any, method: str) -> None:
        spec = titled_spec({"id": 1})
        response = send(SpecView.as_view(spec=spec), method, ada, {"title": "x"})
        assert response.status_code == 405
        assert response["Allow"] == "POST, OPTIONS"
        assert spec.service.calls == []

    def test_options_keeps_djangos_answer(self, ada: Any) -> None:
        response = send(SpecView.as_view(spec=titled_spec()), "OPTIONS", ada)
        assert response.status_code == 200
        assert response["Allow"] == "POST, OPTIONS"

    def test_named_methods_replace_the_default(self, ada: Any) -> None:
        # Spelled in either case, as a reader copying them from a request would.
        view = SpecView.as_view(spec=titled_spec({"id": 1}), methods=["PUT", "patch"])
        assert send(view, "PUT", ada, {"title": "x"}).status_code == 200
        assert send(view, "PATCH", ada, {"title": "x"}).status_code == 200
        refused = send(view, "POST", ada, {"title": "x"})
        assert (refused.status_code, refused["Allow"]) == (405, "PUT, PATCH, OPTIONS")


class TestServing:
    def test_the_url_kwargs_are_the_routes_arguments(self, ada: Any) -> None:
        note = Note.objects.get(owner=ada)
        response = send(SpecView.as_view(spec=note_spec()), "GET", ada, pk=str(note.pk))
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    @pytest.mark.parametrize("policy", list(UnknownArguments))
    def test_every_url_kwarg_is_an_argument_the_spec_must_declare(
        self, ada: Any, policy: UnknownArguments
    ) -> None:
        # The route is the host's, so the mismatch is wrong for every request
        # and propagates to the host's error handling, whatever the policy on
        # what a client sends.
        view = SpecView.as_view(spec=notes_spec(), unknown_arguments=policy)
        with pytest.raises(ImproperlyConfigured, match="The route captures org,"):
            send(view, "GET", ada, org="acme")

    def test_the_views_settings_reach_dispatch(self, ada: Any) -> None:
        spec = titled_spec({"id": 1})
        view = SpecView.as_view(
            spec=spec,
            success_status=201,
            unknown_arguments=UnknownArguments.IGNORE,
            pool_seeds=TENANT_SEEDS,
        )
        response = send(view, "POST", ada, {"title": "x", "colour": "red"})
        assert (response.status_code, body(response)) == (201, {"id": 1})
        assert spec.service.calls[0]["tenant"] == "tenant-of-ada"

    def test_a_subclass_may_declare_its_spec(self, ada: Any) -> None:
        class Notes(SpecView):
            spec = notes_spec()

        assert send(Notes.as_view(), "GET", ada).status_code == 200

    def test_it_is_a_sync_view(self) -> None:
        assert SpecView.view_is_async is False


class TestConfiguration:
    def test_a_view_with_no_spec_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecView.as_view()
        assert str(refused.value) == NO_SPEC.format(got="NoneType")

    def test_a_spec_that_is_not_a_spec_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecView.as_view(spec=notes_spec)
        assert str(refused.value) == NO_SPEC.format(got="function")

    def test_a_method_it_cannot_answer_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecView.as_view(spec=notes_spec(), methods=["get", "options", "trace"])
        assert str(refused.value) == (
            "SpecView.methods names ['options', 'trace'], which no spec is dispatched on; "
            "name any of get, head, post, put, patch, delete."
        )

    def test_a_misspelt_setting_is_djangos_own_refusal(self) -> None:
        # Django's check runs first, so a typo is named as a typo rather than
        # read as a view with no spec.
        with pytest.raises(TypeError, match="invalid keyword 'sepc'"):
            SpecView.as_view(sepc=notes_spec())


class TestCsrf:
    def test_the_hosts_middleware_protects_it(self, ada: Any) -> None:
        # Nothing is exempted: Django's own middleware refuses a POST carrying
        # no token, as it would for any view the host wrote.
        view = SpecView.as_view(spec=titled_spec({"id": 1}))
        request = signed_in(FACTORY.post("/", data={"title": "x"}), ada)
        refused = CsrfViewMiddleware(view).process_view(request, view, (), {})
        assert refused is not None
        assert refused.status_code == 403

    def test_csrf_exempt_is_the_hosts_one_line(self, ada: Any) -> None:
        view = csrf_exempt(SpecView.as_view(spec=titled_spec({"id": 1})))
        request = signed_in(FACTORY.post("/", data={"title": "x"}), ada)
        assert CsrfViewMiddleware(view).process_view(request, view, (), {}) is None
        assert view(request).status_code == 200
