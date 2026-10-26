"""``SpecView``: a spec served as a class-based view, methods and all."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt
from django_service_specs import (
    DEFAULT_POOL_SEEDS,
    DataclassValidator,
    Parameter,
    Parameters,
    PoolSeeds,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceSpec,
    UnknownArguments,
)

from django_spec_html.http.spec_view import SpecView
from tests.dispatch.utils import OPEN, TENANT_SEEDS, Record, make_user
from tests.dispatch_app.models import Note
from tests.http.utils import (
    DEEP_JSON,
    FACTORY,
    Foreign,
    SeesTenant,
    body,
    limited_spec,
    note_spec,
    notes_spec,
    reflected_tenant_spec,
    signed_in,
    titled_spec,
)

pytestmark = pytest.mark.django_db

NO_SPEC = (
    "SpecView.as_view() needs a spec: expected a ServiceSpec or SelectorSpec, or an object "
    "with as_kernel_spec(); got {got}."
)


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

    def test_a_body_nested_past_the_decoder_is_the_clients_400(self, ada: Any) -> None:
        # The decoder raises RecursionError, which no refusal above
        # request_arguments catches, so unless it is refused there the
        # request is the host's 500 rather than the client's 400.
        spec = titled_spec({"id": 1})
        request = FACTORY.generic("POST", "/", data=DEEP_JSON, content_type="application/json")
        response = SpecView.as_view(spec=spec)(signed_in(request, ada))
        assert (response.status_code, body(response)) == (
            400,
            {"non_field_errors": ["The request body is not valid JSON."]},
        )
        assert spec.service.calls == []

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

    def test_an_object_compiling_to_itself_that_is_no_spec_is_refused(self) -> None:
        class SelfCompiling:
            def as_kernel_spec(self) -> SelfCompiling:
                return self

        with pytest.raises(ImproperlyConfigured, match="SelfCompiling.as_kernel_spec.. answers"):
            SpecView.as_view(spec=SelfCompiling())

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


def configured(**attributes: Any) -> Any:
    """A view of a ``SpecView`` subclass setting ``attributes``, which a tree without them ignores."""
    return type("Configured", (SpecView,), attributes).as_view()


def query(view: Any, user: Any, path: str, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get(path), user), **url_kwargs)


def form_post(view: Any, user: Any, fields: dict[str, Any], **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.post("/", data=fields), user), **url_kwargs)


def notes_in(*, user: Any, tenant: str = "") -> Any:
    return Note.objects.filter(owner=user)


TENANT_READ = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=notes_in,
    reads=Parameters.of(Parameter("tenant", "string")),
    permissions=OPEN,
)
"""A read declaring ``tenant``, which ``TENANT_SEEDS`` registers as a seed."""


class TestRouteKwargs:
    def test_a_kwarg_mapped_to_none_is_dropped(self, ada: Any) -> None:
        class Notes(SpecView):
            spec = notes_spec()
            route_kwargs = {"org": None}

        response = send(Notes.as_view(), "GET", ada, org="acme")
        assert (response.status_code, body(response)) == (200, [{"title": "One"}])

    def test_a_kwarg_is_renamed_to_the_parameter_it_fills(self, ada: Any) -> None:
        note = Note.objects.get(owner=ada)
        view = configured(spec=note_spec(), route_kwargs={"note": "pk"})
        response = send(view, "GET", ada, note=str(note.pk))
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_renamed_kwarg_that_does_not_parse_is_the_clients_400(self, ada: Any) -> None:
        # The client's URL chose it, so it is refused as anything a client sent is.
        view = configured(spec=note_spec(), route_kwargs={"note": "pk"})
        response = send(view, "GET", ada, note="four")
        assert (response.status_code, body(response)) == (400, {"pk": ["Enter a whole number."]})

    def test_a_tuple_allows_its_names_and_drops_the_rest(self, ada: Any) -> None:
        note = Note.objects.get(owner=ada)
        view = configured(spec=note_spec(), route_kwargs=("pk",))
        response = send(view, "GET", ada, org="acme", pk=str(note.pk))
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_bare_string_is_refused_at_as_view(self) -> None:
        with pytest.raises(TypeError, match="route_kwargs is a tuple of names"):
            configured(spec=notes_spec(), route_kwargs="org")

    def test_a_target_the_spec_does_not_declare_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="maps a kwarg to team, which the spec"):
            configured(spec=notes_spec(), route_kwargs={"org": "team"})

    def test_an_allowed_name_the_spec_does_not_declare_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="allows team, which the spec"):
            configured(spec=note_spec(), route_kwargs=("pk", "team"))

    def test_route_kwargs_passed_to_as_view_are_checked_there_too(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="maps a kwarg to team, which the spec"):
            SpecView.as_view(spec=notes_spec(), route_kwargs={"org": "team"})

    def test_two_kwargs_onto_one_parameter_are_refused_per_request(self, ada: Any) -> None:
        # Mounted on two routes, a view may capture ``note`` on one and ``pk``
        # on the other, so only a request capturing both is wrong.
        note = Note.objects.get(owner=ada)
        view = configured(spec=note_spec(), route_kwargs={"note": "pk"})
        assert send(view, "GET", ada, pk=str(note.pk)).status_code == 200
        with pytest.raises(ImproperlyConfigured, match="fills pk from more than one kwarg"):
            send(view, "GET", ada, pk=str(note.pk), note=str(note.pk))

    def test_a_kwarg_the_mapping_does_not_name_is_still_refused(self, ada: Any) -> None:
        view = configured(spec=notes_spec(), route_kwargs={"org": None})
        with pytest.raises(ImproperlyConfigured, match="The route captures team,"):
            send(view, "GET", ada, org="acme", team="a")


class TestPoolSeeds:
    def test_a_per_call_seed_occupying_a_bound_name_is_refused(self, ada: Any) -> None:
        # Declared in ``pool_seeds``, the as_view check would refuse this
        # binding; added per call, every request would answer a 400.
        class Seeded(SpecView):
            spec = reflected_tenant_spec()
            server_arguments = ServerArguments(values={"tenant": "server"})

            def get_pool_seeds(self, request: Any) -> PoolSeeds:
                return self.pool_seeds.extend(tenant=lambda: "seeded")

        with pytest.raises(ImproperlyConfigured) as refused:
            query(Seeded.as_view(), ada, "/")
        assert str(refused.value).startswith(
            "Seeded.get_pool_seeds() adds tenant, which server_arguments binds or withholds, "
            "and the check when the view was built never saw a seed added per request"
        )
        with pytest.raises(ImproperlyConfigured, match="a registered pool seed fills"):
            Seeded.as_view(pool_seeds=TENANT_SEEDS)

    def test_a_per_call_seed_occupying_a_withheld_name_is_refused(self, ada: Any) -> None:
        # Withheld and seeded, the withholding would do nothing.
        class Seeded(SpecView):
            spec = reflected_tenant_spec()
            server_arguments = ServerArguments(withheld=frozenset({"tenant"}))

            def get_pool_seeds(self, request: Any) -> PoolSeeds:
                return self.pool_seeds.extend(tenant=lambda: "seeded")

        with pytest.raises(ImproperlyConfigured, match="adds tenant, which server_arguments"):
            query(Seeded.as_view(), ada, "/")

    def test_a_declared_seed_a_bound_validator_field_shares_a_name_with_is_served(
        self, ada: Any
    ) -> None:
        # The kernel lets a Validator field named like a registered seed be
        # bound: it is the caller's argument, and the seed keeps the service's
        # parameter of its name. Only a seed added per call is refused.
        @dataclasses.dataclass
        class Tenanted:
            title: str
            tenant: str

        spec = ServiceSpec(
            service=Record({"id": 1}), permissions=OPEN, validator=DataclassValidator(Tenanted)
        )
        view = SpecView.as_view(
            spec=spec,
            pool_seeds=TENANT_SEEDS,
            server_arguments=ServerArguments(values={"tenant": "acme"}),
        )
        assert form_post(view, ada, {"title": "x", "tenant": "client"}).status_code == 200
        assert spec.service.calls[0]["data"].tenant == "acme"
        assert spec.service.calls[0]["tenant"] == "tenant-of-ada"

    def test_a_per_call_seed_beside_the_binding_is_served(self, ada: Any) -> None:
        check = SeesTenant()

        class Seeded(SpecView):
            spec = note_spec(permissions=(check,))
            server_arguments = ServerArguments(values={"pk": Note.objects.get(owner=ada).pk})

            def get_pool_seeds(self, request: Any) -> PoolSeeds:
                return self.pool_seeds.extend(tenant=lambda: "seeded")

        assert query(Seeded.as_view(), ada, "/").status_code == 200
        assert check.tenants == ["seeded"]

    @pytest.mark.parametrize("hook", ["get_pool_seeds", "get_server_arguments"])
    def test_an_async_hook_is_refused_at_as_view(self, hook: str) -> None:
        # Called without being awaited, it would answer a coroutine.
        async def asynchronous(self: Any, request: Any) -> Any: ...

        view = type("Hooked", (SpecView,), {"spec": notes_spec(), hook: asynchronous})
        with pytest.raises(ImproperlyConfigured) as refused:
            view.as_view()
        assert str(refused.value).startswith(f"Hooked.{hook} is async")
        with pytest.raises(ImproperlyConfigured, match=f"SpecView.{hook} is async"):
            SpecView.as_view(spec=notes_spec(), **{hook: asynchronous})

    def test_a_seed_from_the_request_reaches_the_permission_check(self, ada: Any) -> None:
        check = SeesTenant()

        class Tenanted(SpecView):
            spec = notes_spec(permissions=(check,))
            pool_seeds = TENANT_SEEDS

            def get_pool_seeds(self, request: Any) -> PoolSeeds:
                return DEFAULT_POOL_SEEDS.extend(tenant=lambda: request.headers["X-Tenant"])

        request = signed_in(FACTORY.get("/", headers={"X-Tenant": "acme"}), ada)
        assert Tenanted.as_view()(request).status_code == 200
        # The static seeds would have answered "tenant-of-ada".
        assert check.tenants == ["acme"]

    def test_by_default_the_seeds_are_the_views_own(self, ada: Any) -> None:
        check = SeesTenant()
        view = SpecView.as_view(spec=notes_spec(permissions=(check,)), pool_seeds=TENANT_SEEDS)
        assert send(view, "GET", ada).status_code == 200
        assert check.tenants == ["tenant-of-ada"]


class TestServerArguments:
    def test_a_bound_value_outranks_a_malformed_one_in_the_query(self, ada: Any) -> None:
        # Malformed, so a value coerced before the binding drops it is refused
        # as the client's 400 for a name it was never offered.
        note = Note.objects.get(owner=ada)

        class Bound(SpecView):
            spec = note_spec()
            server_arguments = ServerArguments(values={"pk": note.pk})

        response = query(Bound.as_view(), ada, "/?pk=abc")
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_bound_value_outranks_a_posted_one(self, ada: Any) -> None:
        spec = titled_spec({"id": 1})
        view = configured(spec=spec, server_arguments=ServerArguments(values={"title": "S"}))
        assert form_post(view, ada, {"title": "client"}).status_code == 200
        assert spec.service.calls[0]["data"] == {"title": "S"}

    def test_a_bound_value_outranks_the_one_in_a_json_body(self, ada: Any) -> None:
        spec = titled_spec({"id": 1})
        view = configured(spec=spec, server_arguments=ServerArguments(values={"title": "S"}))
        assert send(view, "POST", ada, {"title": "client"}).status_code == 200
        assert spec.service.calls[0]["data"] == {"title": "S"}

    def test_a_withheld_name_is_dropped_however_malformed(self, ada: Any) -> None:
        Note.objects.create(owner=ada, title="Two")
        view = configured(
            spec=limited_spec(), server_arguments=ServerArguments(withheld=frozenset({"limit"}))
        )
        response = query(view, ada, "/?limit=abc")
        assert (response.status_code, body(response)) == (200, [{"title": "One"}, {"title": "Two"}])
        # Well formed, it would have answered nothing.
        assert body(query(view, ada, "/?limit=0")) == [{"title": "One"}, {"title": "Two"}]

    def test_a_route_capturing_a_withheld_name_is_refused(self, ada: Any) -> None:
        view = configured(
            spec=limited_spec(), server_arguments=ServerArguments(withheld=frozenset({"limit"}))
        )
        with pytest.raises(ImproperlyConfigured) as refused:
            query(view, ada, "/", limit="abc")
        assert str(refused.value).startswith(
            "The route captures limit, which server_arguments answers for. "
            "Drop each with route_kwargs={'limit': None}"
        )

    def test_a_route_kwarg_mapped_onto_a_bound_name_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            configured(
                spec=note_spec(),
                route_kwargs={"note": "pk", "org": None},
                server_arguments=ServerArguments(values={"pk": 1}),
            )
        assert str(refused.value).startswith(
            "Configured.route_kwargs fills pk, which server_arguments answers for."
        )

    def test_an_allowed_name_the_server_withholds_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="route_kwargs fills limit, which"):
            configured(
                spec=limited_spec(),
                route_kwargs=("limit",),
                server_arguments=ServerArguments(withheld=frozenset({"limit"})),
            )

    def test_a_bound_name_the_route_drops_is_served(self, ada: Any) -> None:
        note = Note.objects.get(owner=ada)
        view = configured(
            spec=note_spec(),
            route_kwargs={"pk": None},
            server_arguments=ServerArguments(values={"pk": note.pk}),
        )
        response = query(view, ada, "/", pk="999")
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_binding_that_cannot_mean_what_it_says_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="ServerArguments names nope, which the"):
            SpecView.as_view(
                spec=notes_spec(), server_arguments=ServerArguments(values={"nope": 1})
            )

    def test_a_subclass_declaring_one_is_checked_at_as_view_too(self) -> None:
        class Bound(SpecView):
            spec = notes_spec()
            server_arguments = ServerArguments(withheld=frozenset({"nope"}))

        with pytest.raises(ImproperlyConfigured, match="ServerArguments names nope, which the"):
            Bound.as_view()

    def test_the_check_reads_the_views_declared_seeds(self) -> None:
        # ``tenant`` is free under the default seeds and a seed's under these.
        bound = ServerArguments(values={"tenant": "acme"})
        SpecView.as_view(spec=TENANT_READ, server_arguments=bound)
        with pytest.raises(ImproperlyConfigured, match="which a registered pool seed occupies"):
            SpecView.as_view(spec=TENANT_READ, server_arguments=bound, pool_seeds=TENANT_SEEDS)

        class Seeded(SpecView):
            pool_seeds = TENANT_SEEDS

        with pytest.raises(ImproperlyConfigured, match="which a registered pool seed occupies"):
            Seeded.as_view(spec=TENANT_READ, server_arguments=bound)

    def test_a_view_with_no_spec_is_refused_as_one_whatever_it_binds(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecView.as_view(server_arguments=ServerArguments(values={"pk": 1}))
        assert str(refused.value) == NO_SPEC.format(got="NoneType")

    def test_a_per_call_binding_of_the_same_names_is_served(self, ada: Any) -> None:
        note = Note.objects.get(owner=ada)

        class PerCall(SpecView):
            spec = note_spec()
            server_arguments = ServerArguments(values={"pk": 0})

            def get_server_arguments(self, request: Any) -> ServerArguments | None:
                return ServerArguments(values={"pk": int(request.headers["X-Note"])})

        request = signed_in(FACTORY.get("/?pk=abc", headers={"X-Note": str(note.pk)}), ada)
        response = PerCall.as_view()(request)
        assert (response.status_code, body(response)) == (200, {"title": "One"})

    def test_a_per_call_binding_dropping_a_declared_name_is_refused(self, ada: Any) -> None:
        # Otherwise one override un-binds a name the server answers for, and
        # the client's value for it reaches dispatch.
        class Unbound(SpecView):
            spec = note_spec()
            server_arguments = ServerArguments(values={"pk": 1})

            def get_server_arguments(self, request: Any) -> ServerArguments | None:
                return None

        with pytest.raises(ImproperlyConfigured) as refused:
            query(Unbound.as_view(), ada, "/?pk=1")
        assert str(refused.value).startswith(
            "Unbound.get_server_arguments() binds nothing and withholds nothing, where "
            "server_arguments binds pk and withholds nothing."
        )

    def test_a_per_call_binding_withholding_a_name_declared_bound_is_refused(
        self, ada: Any
    ) -> None:
        # The same names, so the declared check's verdict on what may be
        # withheld - a required ``pk`` may not - would not hold for this one.
        class Withheld(SpecView):
            spec = note_spec()
            server_arguments = ServerArguments(values={"pk": 1})

            def get_server_arguments(self, request: Any) -> ServerArguments | None:
                return ServerArguments(withheld=frozenset({"pk"}))

        with pytest.raises(ImproperlyConfigured) as refused:
            query(Withheld.as_view(), ada, "/?pk=1")
        assert str(refused.value).startswith(
            "Withheld.get_server_arguments() binds nothing and withholds pk, where "
            "server_arguments binds pk and withholds nothing."
        )

    def test_a_per_call_binding_releasing_a_withheld_name_is_refused(self, ada: Any) -> None:
        # Nothing bound on either side, so only the withheld names differ.
        class Released(SpecView):
            spec = limited_spec()
            server_arguments = ServerArguments(withheld=frozenset({"limit"}))

            def get_server_arguments(self, request: Any) -> ServerArguments | None:
                return None

        with pytest.raises(ImproperlyConfigured) as refused:
            query(Released.as_view(), ada, "/?limit=0")
        assert str(refused.value).startswith(
            "Released.get_server_arguments() binds nothing and withholds nothing, where "
            "server_arguments binds nothing and withholds limit."
        )

    def test_an_empty_binding_and_none_stand_for_each_other(self, ada: Any) -> None:
        class Empty(SpecView):
            spec = notes_spec()
            server_arguments = ServerArguments()

            def get_server_arguments(self, request: Any) -> ServerArguments | None:
                return None

        assert query(Empty.as_view(), ada, "/").status_code == 200

    def test_a_per_call_binding_naming_an_undeclared_name_is_refused(self, ada: Any) -> None:
        # The as_view check never saw it, so it could name anything.
        class Unchecked(SpecView):
            spec = note_spec()

            def get_server_arguments(self, request: Any) -> ServerArguments | None:
                return ServerArguments(values={"pk": 1}, withheld=frozenset({"nope"}))

        with pytest.raises(ImproperlyConfigured) as refused:
            query(Unchecked.as_view(), ada, "/?pk=1")
        assert str(refused.value).startswith(
            "Unchecked.get_server_arguments() binds pk and withholds nope, where "
            "server_arguments binds nothing and withholds nothing."
        )


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


class TestSources:
    def test_a_selector_source_answers_get_and_refuses_post(self, ada: Any) -> None:
        view = SpecView.as_view(spec=Foreign(notes_spec()))

        assert send(view, "GET", ada).status_code == 200
        assert (send(view, "POST", ada).status_code, send(view, "POST", ada)["Allow"]) == (
            405,
            "GET, HEAD, OPTIONS",
        )

    def test_a_service_source_answers_post_as_its_compiled_spec(self, ada: Any) -> None:
        view = SpecView.as_view(spec=Foreign(titled_spec(None)))

        response = send(view, "POST", ada, {"title": "x"})

        assert (response.status_code, response.content) == (204, b"")

    def test_a_source_whose_compile_is_wrong_is_refused_at_as_view(self) -> None:
        class Wrong:
            def as_kernel_spec(self) -> Any:
                return "a spec"

        with pytest.raises(ImproperlyConfigured) as refused:
            SpecView.as_view(spec=Wrong())
        assert str(refused.value) == (
            "SpecView.as_view() needs a spec: Wrong.as_kernel_spec() must return a ServiceSpec "
            "or SelectorSpec; got str."
        )
