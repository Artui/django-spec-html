"""``SpecConfirmView``: a spec with no Validator, served as the page that confirms it.

Assertions read ``response.context_data`` - what a template is handed - and,
for what the template draws, the rendered body. A spec callable that records
its pool is the service throughout, so a test reads back exactly what
dispatch handed it. The module is its own URLconf, since ``resolve_url`` reads
the project's.
"""

from __future__ import annotations

import dataclasses
import types
from collections.abc import Callable, Mapping
from typing import Annotated, Any, TypedDict

import django
import pytest
from django import forms
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import BadRequest, ImproperlyConfigured, PermissionDenied
from django.db.models import QuerySet
from django.http import Http404, HttpResponse
from django.test import Client
from django.urls import path
from django_service_specs import (
    UNSET,
    ActionUnavailable,
    AdditionalInputRequired,
    Affordance,
    DataclassPresenter,
    DispatchResult,
    FieldMarking,
    FormValidator,
    NotPermitted,
    Parameter,
    Parameters,
    PoolSeeds,
    SelectorKind,
    SelectorSpec,
    ServerArguments,
    ServiceConflict,
    ServiceNotFound,
    ServiceSpec,
    ServiceValidationError,
    UnsetType,
)

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import (
    OPEN,
    PK,
    TENANT_SEEDS,
    OwnerOnly,
    Record,
    Refuse,
    Titled,
    make_user,
    note_by_pk,
)
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, HANDED_STAGES, Handed, handed_spec, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

PAGE = "spec_confirm_view.html"
FRAGMENT = "spec_confirm_view_form.html"
HTMX = {"HTTP_HX_REQUEST": "true"}
# What a browser posts from the page: the token, and the button pressed.
PRESSED = {"withdraw": ""}


@dataclasses.dataclass
class NoteOut:
    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]
    archived: bool


def archive(*, instance: Note, **pool: Any) -> Note:
    instance.archived = True
    instance.save(update_fields=["archived"])
    return instance


class Archives(Record):
    """Records its pool, as ``Record`` does, and archives the note it acts on."""

    def __call__(self, **pool: Any) -> Any:
        super().__call__(**pool)
        return archive(**pool)


ONE_NOTE = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK)


def archive_spec(**kwargs: Any) -> ServiceSpec:
    """Archive the note the route names: no Validator, and one required read, the target's ``pk``."""
    kwargs.setdefault("service", Archives())
    kwargs.setdefault("permissions", OPEN)
    kwargs.setdefault("instance_selector_spec", ONE_NOTE)
    return ServiceSpec(**kwargs)


def confirm_view(spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs.setdefault("template_name", PAGE)
    initkwargs.setdefault("success_url", "/done/")
    if "pk" in spec.parameters().names():
        initkwargs.setdefault("route_kwargs", ("pk",))
    return SpecConfirmView.as_view(spec=spec, **initkwargs)


def get(view: Any, user: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user), **url_kwargs)


def post(view: Any, user: Any, data: Mapping[str, Any] = PRESSED, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user), **url_kwargs)


def calls(spec: ServiceSpec) -> list[dict[str, Any]]:
    return spec.service.calls


def raising(exc: Exception, **kwargs: Any) -> ServiceSpec:
    def service(**pool: Any) -> None:
        raise exc

    return archive_spec(service=service, **kwargs)


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def note(ada: Any) -> Note:
    return Note.objects.create(owner=ada, title="Buy milk")


class TestTheSpecsItServes:
    def test_a_spec_with_a_form_is_refused_and_pointed_at_the_form_view(self) -> None:
        class NoteForm(forms.Form):
            title = forms.CharField()

        spec = archive_spec(validator=FormValidator(NoteForm))
        with pytest.raises(ImproperlyConfigured, match=r"SpecConfirmView .* SpecFormView"):
            confirm_view(spec)

    def test_any_other_validator_is_refused_naming_it(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"validator=None; got Titled"):
            confirm_view(archive_spec(validator=Titled()))

    def test_the_form_view_refuses_a_spec_with_no_validator(self) -> None:
        # The review's row 7: the form view refuses the spec this view serves,
        # at URLconf import, naming what it found.
        with pytest.raises(ImproperlyConfigured, match="must be a FormValidator; got NoneType"):
            SpecFormView.as_view(spec=archive_spec(), template_name=PAGE, success_url="/done/")


class Scope(TypedDict):
    owner_name: str


class MayDecline(TypedDict):
    owner_name: str | UnsetType


class TestARequiredParameterIsSupplied:
    """``as_view()`` refuses a required parameter no source supplies, since no post carries one."""

    @staticmethod
    def spec(*reads: Parameter, **kwargs: Any) -> ServiceSpec:
        return ServiceSpec(
            service=Record(), permissions=OPEN, reads=Parameters.of(*reads), **kwargs
        )

    def test_a_required_read_nothing_supplies_is_refused_naming_it(self) -> None:
        spec = self.spec(Parameter("reason", "string", required=True))
        with pytest.raises(ImproperlyConfigured, match=r"requires reason, which no post"):
            confirm_view(spec)

    def test_every_one_is_named_in_order(self) -> None:
        spec = self.spec(
            Parameter("reason", "string", required=True),
            Parameter("by", "string", required=True),
        )
        with pytest.raises(ImproperlyConfigured, match=r"requires by, reason, which"):
            confirm_view(spec)

    @pytest.mark.parametrize(
        "route_kwargs",
        [("pk",), {"org": None, "pk": "pk"}, {"note": "pk"}],
        ids=["allowed", "kept", "renamed"],
    )
    def test_the_route_supplies_what_route_kwargs_names(self, route_kwargs: Any) -> None:
        confirm_view(archive_spec(), route_kwargs=route_kwargs)

    @pytest.mark.parametrize(
        "route_kwargs", [None, {"pk": None}, {"org": None}], ids=["strict", "dropped", "unnamed"]
    )
    def test_a_route_naming_no_kwarg_into_it_supplies_nothing(self, route_kwargs: Any) -> None:
        # Under the strict rule the route is not known when the URLconf is
        # imported, so it supplies nothing until route_kwargs names it.
        with pytest.raises(
            ImproperlyConfigured, match=r"requires pk, which no post .* route_kwargs"
        ):
            SpecConfirmView.as_view(
                spec=archive_spec(), template_name=PAGE, success_url="/", route_kwargs=route_kwargs
            )

    def test_a_registered_seed_supplies_a_reflected_read(self) -> None:
        spec = self.spec(Parameter("tenant", "string", required=True, reflected=True))
        confirm_view(spec, pool_seeds=TENANT_SEEDS)
        with pytest.raises(ImproperlyConfigured, match=r"requires tenant"):
            confirm_view(spec)

    def test_a_seed_only_get_pool_seeds_adds_is_not_seen(self) -> None:
        # Asked at URLconf import, with no request: the declared seeds alone.
        class PerCall(SpecConfirmView):
            def get_pool_seeds(self, request: Any) -> PoolSeeds:
                return TENANT_SEEDS

        spec = self.spec(Parameter("tenant", "string", required=True, reflected=True))
        with pytest.raises(ImproperlyConfigured, match=r"requires tenant"):
            PerCall.as_view(spec=spec, template_name=PAGE, success_url="/")

    def test_a_bound_server_argument_supplies_it(self) -> None:
        spec = self.spec(Parameter("reason", "string", required=True))
        confirm_view(spec, server_arguments=ServerArguments(values={"reason": "tidy"}))

    def test_withholding_it_is_refused_by_the_kernel_s_own_check(self) -> None:
        spec = self.spec(Parameter("reason", "string", required=True))
        with pytest.raises(ImproperlyConfigured, match=r"ServerArguments withholds reason") as e:
            confirm_view(spec, server_arguments=ServerArguments(withheld=frozenset({"reason"})))
        assert "no post" not in str(e.value)

    def test_a_typed_provider_supplies_the_key_it_fills(self) -> None:
        def scope() -> Scope:
            return {"owner_name": "ada"}

        confirm_view(self.spec(Parameter("owner_name", "string", required=True), kwargs=scope))

    def test_a_key_a_typed_provider_may_decline_is_not_demanded(self) -> None:
        # Not among the keys the server owns, which leave a declinable one to
        # the caller: the shape check's relaxing alone lets it through.
        def scope() -> MayDecline:
            return {"owner_name": UNSET}

        confirm_view(self.spec(Parameter("owner_name", "string", required=True), kwargs=scope))

    def test_an_untyped_provider_supplies_every_read_it_reaches(self) -> None:
        spec = self.spec(Parameter("owner_name", "string", required=True), kwargs=dict)
        confirm_view(spec)

    @pytest.mark.parametrize(
        "read",
        [Parameter("reason", "string"), Parameter("reason", "string", required=True, default="")],
        ids=["optional", "defaulted"],
    )
    def test_a_read_the_shape_check_does_not_demand_needs_no_source(self, read: Any) -> None:
        confirm_view(self.spec(read))

    def test_a_bare_string_route_kwargs_is_refused_as_a_string(self) -> None:
        with pytest.raises(TypeError, match=r"not one string"):
            SpecConfirmView.as_view(
                spec=archive_spec(), template_name=PAGE, success_url="/", route_kwargs="pk"
            )


class TestThePage:
    def test_get_renders_the_target_and_a_button_with_no_form(self, ada: Any, note: Note) -> None:
        response = get(confirm_view(archive_spec()), ada, pk=note.pk)
        assert response.status_code == 200
        context = response.context_data
        assert context["target"] == note
        assert "form" not in context
        assert "errors" not in context
        assert b'<button name="withdraw">' in response.render().content

    def test_the_target_is_displayed_where_the_page_opts_in(self, ada: Any, note: Note) -> None:
        spec = archive_spec(presenter=DataclassPresenter(NoteOut))
        response = get(confirm_view(spec, display_target=True), ada, pk=note.pk)
        assert response.context_data["target_display"] == {
            "label": "Buy milk",
            "fields": {"title": "Buy milk", "archived": False},
            "pairs": tuple({"title": "Buy milk", "archived": False}.items()),
        }
        assert b"Withdraw <strong>Buy milk</strong>?" in response.render().content

    def test_a_set_is_counted(self, ada: Any, note: Note) -> None:
        spec = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=lambda: Note.objects.all()
            ),
        )
        context = get(confirm_view(spec), ada).context_data
        assert (context["target_count"], "target_counted_at" in context) == (1, True)

    def test_an_unmet_affordance_is_named(self, ada: Any, note: Note) -> None:
        paused = Affordance(code="paused", reason="Archiving is paused.", when=lambda: False)
        response = get(confirm_view(archive_spec(affordances=[paused])), ada, pk=note.pk)
        assert response.context_data["unavailable"] is paused
        assert b'<button name="withdraw" disabled>' in response.render().content

    def test_a_principal_the_spec_refuses_is_denied_on_either_method(
        self, ada: Any, note: Note
    ) -> None:
        view = confirm_view(archive_spec(permissions=[Refuse()]))
        for answer in (get, post):
            with pytest.raises(PermissionDenied, match="Only editors"):
                answer(view, ada, pk=note.pk)

    def test_a_row_the_object_check_refuses_is_denied(self, note: Note) -> None:
        view = confirm_view(archive_spec(permissions=[OwnerOnly()]))
        with pytest.raises(PermissionDenied, match="Only the owner"):
            post(view, make_user("bo"), pk=note.pk)

    def test_a_missing_row_is_not_found(self, ada: Any) -> None:
        with pytest.raises(Http404):
            get(confirm_view(archive_spec()), ada, pk=404)

    def test_a_route_value_the_reads_refuse_is_a_bad_request_on_get(self, ada: Any) -> None:
        with pytest.raises(BadRequest):
            get(confirm_view(archive_spec()), ada, pk="seven")

    def test_htmx_gets_the_fragment(self, ada: Any, note: Note) -> None:
        view = confirm_view(archive_spec(), fragment_template_name=FRAGMENT)
        response = view(signed_in(FACTORY.get("/", **HTMX), ada), pk=note.pk)
        body = response.render().content
        assert (body.startswith(b'<div id="withdraw">'), b"<html>" in body) == (True, False)
        assert {"HX-Request", "HX-Boosted", "HX-History-Restore-Request"} <= set(
            name.strip() for name in response["Vary"].split(",")
        )


class TestAPostThatCommits:
    def test_it_runs_on_the_route_s_row_and_redirects(self, ada: Any, note: Note) -> None:
        spec = archive_spec()
        response = post(confirm_view(spec), ada, pk=note.pk)
        assert (response.status_code, response.url) == (302, "/done/")
        note.refresh_from_db()
        assert note.archived
        assert calls(spec)[0]["instance"] == note

    def test_nothing_posted_becomes_an_argument(self, ada: Any, note: Note) -> None:
        other = Note.objects.create(owner=ada, title="Other")
        read = Parameter("confirmed", "boolean")
        spec = archive_spec(reads=Parameters.of(read))
        data = {**PRESSED, "pk": str(other.pk), "confirmed": "true", "title": "x"}
        post(confirm_view(spec), ada, data, pk=note.pk)
        pool = calls(spec)[0]
        assert pool["instance"] == note
        assert {"confirmed", "title"}.isdisjoint(pool)

    def test_the_seeds_and_the_binding_reach_the_service(self, ada: Any, note: Note) -> None:
        reads = Parameters.of(
            Parameter("tenant", "string", required=True, reflected=True),
            Parameter("reason", "string", required=True),
        )
        spec = archive_spec(reads=reads)
        view = confirm_view(
            spec,
            pool_seeds=TENANT_SEEDS,
            server_arguments=ServerArguments(values={"reason": "tidy"}),
        )
        post(view, ada, pk=note.pk)
        pool = calls(spec)[0]
        assert (pool["tenant"], pool["reason"]) == ("tenant-of-ada", "tidy")

    def test_htmx_is_answered_hx_redirect(self, ada: Any, note: Note) -> None:
        view = confirm_view(archive_spec(), fragment_template_name=FRAGMENT)
        response = view(signed_in(FACTORY.post("/", data=PRESSED, **HTMX), ada), pk=note.pk)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/done/")

    def test_htmx_is_answered_the_result_in_place_where_the_fragment_has_one(
        self, ada: Any, note: Note, settings: Any
    ) -> None:
        # From Django 6.0 the address names a ``partialdef`` inside the file;
        # below it, a template registered under the address itself.
        result = "<p>{{ result.value.title }} archived.</p>"
        if django.VERSION >= (6, 0):
            templates = {
                "fragment.html": f"<form></form>{{% partialdef spec-result %}}{result}"
                "{% endpartialdef %}"
            }
        else:
            templates = {"fragment.html": "<form></form>", "fragment.html#spec-result": result}
        settings.TEMPLATES = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", templates)]},
            }
        ]
        view = confirm_view(archive_spec(), fragment_template_name="fragment.html")
        response = view(signed_in(FACTORY.post("/", data=PRESSED, **HTMX), ada), pk=note.pk)
        assert response["HX-Trigger"] == "spec-succeeded"
        assert response.render().content == b"<p>Buy milk archived.</p>"


class TestAPostThatDoesNotCommit:
    def test_a_post_without_the_button_is_the_page_again(self, ada: Any, note: Note) -> None:
        spec = archive_spec()
        response = post(confirm_view(spec, commit_button="withdraw"), ada, {}, pk=note.pk)
        assert (response.status_code, response.context_data["refreshed"]) == (200, True)
        assert response.context_data["target"] == note
        assert "form" not in response.context_data
        assert calls(spec) == []

    def test_the_button_commits(self, ada: Any, note: Note) -> None:
        spec = archive_spec()
        response = post(confirm_view(spec, commit_button="withdraw"), ada, pk=note.pk)
        assert response.status_code == 302
        assert len(calls(spec)) == 1

    def test_a_trigger_naming_another_posted_control_does_not_commit(
        self, ada: Any, note: Note
    ) -> None:
        spec = archive_spec()
        view = confirm_view(spec, commit_button="withdraw")
        request = FACTORY.post(
            "/", data={**PRESSED, "undo": "1"}, HTTP_HX_TRIGGER_NAME="undo", **HTMX
        )
        view(signed_in(request, ada), pk=note.pk)
        assert calls(spec) == []

    def test_anything_but_a_button_s_name_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="commit_button must be None"):
            confirm_view(archive_spec(), commit_button="")


class TestARefusal:
    """A refused post re-renders the confirmation page, at the map's status, with no messages app."""

    def test_a_conflict_is_the_page_again_with_its_message(self, ada: Any, note: Note) -> None:
        view = confirm_view(raising(ServiceConflict("Already archived.")))
        response = post(view, ada, pk=note.pk)
        assert response.status_code == 409
        context = response.context_data
        assert (context["errors"], context["target"]) == (["Already archived."], note)
        assert "form" not in context
        assert b'<p class="error">Already archived.</p>' in response.render().content

    def test_an_unavailable_action_carries_its_code(self, ada: Any, note: Note) -> None:
        refused = ActionUnavailable("Archiving is paused.", code="paused")
        response = post(confirm_view(raising(refused)), ada, pk=note.pk)
        assert response.status_code == 409
        assert (response.context_data["code"], response.context_data["errors"]) == (
            "paused",
            ["Archiving is paused."],
        )

    def test_a_service_s_tree_is_flattened_with_each_path(self, ada: Any, note: Note) -> None:
        detail = {"non_field_errors": ["Not now."], "where": {"city": ["Unknown."]}}
        response = post(confirm_view(raising(ServiceValidationError(detail))), ada, pk=note.pk)
        assert response.status_code == 400
        assert response.context_data["errors"] == ["Not now.", "where.city: Unknown."]

    def test_a_route_value_the_reads_refuse_is_named_on_post(self, ada: Any) -> None:
        response = post(confirm_view(archive_spec()), ada, pk="seven")
        assert response.status_code == 400
        assert response.context_data["errors"] == ["pk: Enter a whole number."]
        assert "target" not in response.context_data

    def test_htmx_gets_the_fragment_at_200(self, ada: Any, note: Note) -> None:
        view = confirm_view(
            raising(ServiceConflict("Already archived.")), fragment_template_name=FRAGMENT
        )
        response = view(signed_in(FACTORY.post("/", data=PRESSED, **HTMX), ada), pk=note.pk)
        body = response.render().content
        assert (response.status_code, b"<html>" in body) == (200, False)
        assert b"Already archived." in body

    @pytest.mark.parametrize(
        ("refused", "raised"),
        [(NotPermitted("No."), PermissionDenied), (ServiceNotFound("Gone."), Http404)],
        ids=["not-permitted", "not-found"],
    )
    def test_a_denial_is_raised(self, ada: Any, note: Note, refused: Any, raised: Any) -> None:
        with pytest.raises(raised):
            post(confirm_view(raising(refused)), ada, pk=note.pk)


class TestAQuestion:
    """A question is rendered as its message: no post can carry an answer to it."""

    @staticmethod
    def asking() -> ServiceSpec:
        def service(*, instance: Note, confirmed: bool = False, **pool: Any) -> None:
            seen.append(confirmed)
            raise AdditionalInputRequired(
                "Archive it for good?", schema={"confirmed": {"type": "boolean"}}
            )

        seen: list[bool] = []
        spec = archive_spec(service=service, reads=Parameters.of(Parameter("confirmed", "boolean")))
        spec.service.seen = seen  # ty: ignore[unresolved-attribute]
        return spec

    def test_it_is_the_page_at_422_with_the_message(self, ada: Any, note: Note) -> None:
        response = post(confirm_view(self.asking()), ada, pk=note.pk)
        assert response.status_code == 422
        context = response.context_data
        assert context["requested_input"] == {
            "message": "Archive it for good?",
            "schema": {"confirmed": {"type": "boolean"}},
        }
        assert (context["errors"], context["target"]) == (["Archive it for good?"], note)
        assert b'<p class="asked">Archive it for good?</p>' in response.render().content

    def test_a_confirming_post_is_asked_again(self, ada: Any, note: Note) -> None:
        spec = self.asking()
        response = post(confirm_view(spec), ada, {**PRESSED, "confirmed": "true"}, pk=note.pk)
        assert response.status_code == 422
        assert spec.service.seen == [False]  # ty: ignore[unresolved-attribute]

    def test_htmx_gets_the_fragment_at_200(self, ada: Any, note: Note) -> None:
        view = confirm_view(self.asking(), fragment_template_name=FRAGMENT)
        response = view(signed_in(FACTORY.post("/", data=PRESSED, **HTMX), ada), pk=note.pk)
        assert response.status_code == 200
        assert b"Archive it for good?" in response.render().content

    def test_a_question_no_route_could_hear_answered_is_raised(self, ada: Any, note: Note) -> None:
        def service(**pool: Any) -> None:
            raise AdditionalInputRequired("Sure?", schema={"sure": {"type": "boolean"}})

        with pytest.raises(ImproperlyConfigured, match="'sure'"):
            post(confirm_view(archive_spec(service=service)), ada, pk=note.pk)


MESSAGES_STACK = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]


@pytest.fixture
def stack(settings: Any) -> Callable[..., Any]:
    """Post through Django's handler and the messages middleware, at ``/notes/<pk>/``."""
    settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "django.contrib.messages"]
    settings.MIDDLEWARE = MESSAGES_STACK
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

    def posted(view: Any, user: Any, url: str) -> Any:
        urls = types.ModuleType("one_page_urls")
        urls.urlpatterns = [  # ty: ignore[unresolved-attribute]
            path("notes/<int:pk>/", view),
            path("notes/", view),
            *urlpatterns,
        ]
        settings.ROOT_URLCONF = urls
        client = Client()
        client.force_login(user)
        return client.post(url, PRESSED)

    return posted


def sent(response: Any) -> list[tuple[int, str]]:
    return [(each.level, each.message) for each in get_messages(response.wsgi_request)]


def drafts() -> QuerySet[Note]:
    return Note.objects.filter(archived=False).order_by("pk")


class TestASuccessMessage:
    def test_it_is_formatted_with_the_target(self, ada: Any, note: Note, stack: Any) -> None:
        view = confirm_view(archive_spec(), success_message="%(target)s archived.")
        response = stack(view, ada, f"/notes/{note.pk}/")
        assert response.status_code == 302
        assert sent(response) == [(messages.SUCCESS, f"Note object ({note.pk}) archived.")]

    def test_a_set_is_read_as_the_operation_left_it(self, ada: Any, note: Note, stack: Any) -> None:
        # The set is the queryset, read again when the message is formatted:
        # every draft archived, none match it any more.
        def archive_all(*, collection: QuerySet[Note]) -> None:
            collection.update(archived=True)

        spec = ServiceSpec(
            service=archive_all,
            permissions=OPEN,
            collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=drafts),
        )
        view = confirm_view(spec, success_message="Left: %(target)s")
        assert sent(stack(view, ada, "/notes/")) == [(messages.SUCCESS, "Left: <QuerySet []>")]

    def test_a_message_naming_nothing_is_sent_as_written(
        self, ada: Any, note: Note, stack: Any
    ) -> None:
        view = confirm_view(archive_spec(), success_message="Archived.")
        assert sent(stack(view, ada, f"/notes/{note.pk}/")) == [(messages.SUCCESS, "Archived.")]

    def test_a_refusal_sends_none(self, ada: Any, note: Note, stack: Any) -> None:
        view = confirm_view(raising(ServiceConflict("No.")), success_message="Archived.")
        response = stack(view, ada, f"/notes/{note.pk}/")
        assert (response.status_code, sent(response)) == (409, [])

    def test_without_the_middleware_nothing_is_raised(self, ada: Any, note: Note) -> None:
        view = confirm_view(archive_spec(), success_message="%(target)s archived.")
        assert post(view, ada, pk=note.pk).status_code == 302

    def test_get_success_message_reads_the_result(self) -> None:
        view = SpecConfirmView(success_message="%(target)s.")
        result = DispatchResult(kind="instance", instance="Buy milk", data={})
        assert view.get_success_message(result) == "Buy milk."


def test_every_stage_is_handed_the_request_and_the_page(ada: Any, note: Note) -> None:
    # No Validator, so no form's keywords: the target's selector, both
    # checks, the affordance and the service are what name the two here.
    handed = Handed()

    def service(*, request: Any, view: Any) -> None:
        handed.record("service", request, view)

    view = confirm_view(handed_spec(handed, validator=None, service=service))
    request = signed_in(FACTORY.get("/"), ada)
    assert view(request, pk=note.pk).status_code == 200
    assert handed.stages() == HANDED_STAGES - {"form", "service"}
    page = handed.one_view(request)
    assert isinstance(page, SpecConfirmView)
    assert page.request is request
    handed.seen.clear()
    request = signed_in(FACTORY.post("/", data=PRESSED), ada)
    assert view(request, pk=note.pk).status_code == 302
    assert handed.stages() == HANDED_STAGES - {"form"}
    page = handed.one_view(request)
    assert isinstance(page, SpecConfirmView)
    assert page.request is request


class ReturnsToTheList(SpecConfirmView):
    """A confirmation posted from a list's row, sent back to it: set as a host's subclass sets it."""

    template_name = PAGE
    success_url = "/done/"
    route_kwargs = ("pk",)
    redirect_field_name = "next"
    refusal_redirect = True


# The list a row's button was posted from, as its own query names it.
FROM_THE_LIST = "?next=/list/%3Fpage%3D3"
PAUSED = Affordance(code="paused", reason="Archiving is paused.", when=lambda: False)


def from_a_row(
    view: Any, user: Any, pk: Any, query: str = FROM_THE_LIST, **extra: Any
) -> tuple[Any, Any]:
    """Post the row's button with a messages store, as the middleware leaves the request."""
    request = signed_in(FACTORY.post(f"/notes/{pk}/archive/{query}", data=PRESSED, **extra), user)
    request._messages = CookieStorage(request)  # ty: ignore[unresolved-attribute]
    return view(request, pk=pk), request


def stored(request: Any) -> list[tuple[int, str, str]]:
    return [(each.level, each.message, each.extra_tags) for each in get_messages(request)]


class TestASuccessReturnsToNext:
    def test_it_redirects_to_next(self, ada: Any, note: Note) -> None:
        response, _ = from_a_row(ReturnsToTheList.as_view(spec=archive_spec()), ada, note.pk)
        assert (response.status_code, response.url) == (302, "/list/?page=3")

    def test_htmx_is_sent_to_next(self, ada: Any, note: Note) -> None:
        view = ReturnsToTheList.as_view(spec=archive_spec(), fragment_template_name=FRAGMENT)
        response, _ = from_a_row(view, ada, note.pk, **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/list/?page=3")

    def test_an_unsafe_next_is_the_success_url(self, ada: Any, note: Note) -> None:
        view = ReturnsToTheList.as_view(spec=archive_spec())
        response, _ = from_a_row(view, ada, note.pk, "?next=//evil.example")
        assert response.url == "/done/"

    def test_nothing_posted_reaches_dispatch(self, ada: Any, note: Note) -> None:
        spec = archive_spec()
        response, _ = from_a_row(ReturnsToTheList.as_view(spec=spec), ada, note.pk)
        assert response.status_code == 302
        assert "next" not in calls(spec)[0]


class TestARefusalRedirect:
    """An unmet condition on a row's button sends the person back to the list, saying why."""

    def test_an_unmet_affordance_returns_to_next_with_its_reason(
        self, ada: Any, note: Note
    ) -> None:
        view = ReturnsToTheList.as_view(spec=archive_spec(affordances=[PAUSED]))
        response, request = from_a_row(view, ada, note.pk)
        assert (response.status_code, response.url) == (302, "/list/?page=3")
        assert stored(request) == [(messages.ERROR, "Archiving is paused.", "paused")]

    def test_htmx_is_sent_back_with_hx_redirect(self, ada: Any, note: Note) -> None:
        view = ReturnsToTheList.as_view(
            spec=archive_spec(affordances=[PAUSED]), fragment_template_name=FRAGMENT
        )
        response, request = from_a_row(view, ada, note.pk, **HTMX)
        assert (response.status_code, response["HX-Redirect"]) == (200, "/list/?page=3")
        assert not response.has_header("Location")
        assert stored(request) == [(messages.ERROR, "Archiving is paused.", "paused")]

    @pytest.mark.parametrize(
        ("spec", "pk", "sent"),
        [
            (lambda: raising(ServiceConflict("Already archived.")), None, ["Already archived."]),
            (
                lambda: raising(ServiceValidationError({"where": {"city": ["Unknown."]}})),
                None,
                ["where.city: Unknown."],
            ),
            (lambda: archive_spec(), "seven", ["pk: Enter a whole number."]),
            (lambda: TestAQuestion.asking(), None, ["Archive it for good?"]),
        ],
        ids=["failed", "invalid", "invalid-route", "needs-input"],
    )
    def test_each_outcome_the_page_renders_returns_with_each_message(
        self, ada: Any, note: Note, spec: Any, pk: Any, sent: list[str]
    ) -> None:
        view = ReturnsToTheList.as_view(spec=spec())
        response, request = from_a_row(view, ada, note.pk if pk is None else pk)
        assert (response.status_code, response.url) == (302, "/list/?page=3")
        assert stored(request) == [(messages.ERROR, each, "") for each in sent]

    def test_every_message_of_a_tree_is_sent_in_order(self, ada: Any, note: Note) -> None:
        detail = {"non_field_errors": ["Not now."], "where": {"city": ["Unknown."]}}
        view = ReturnsToTheList.as_view(spec=raising(ServiceValidationError(detail)))
        _, request = from_a_row(view, ada, note.pk)
        assert [message for _, message, _ in stored(request)] == [
            "Not now.",
            "where.city: Unknown.",
        ]

    @pytest.mark.parametrize(
        ("refused", "raised"),
        [(NotPermitted("No."), PermissionDenied), (ServiceNotFound("Gone."), Http404)],
        ids=["not-permitted", "not-found"],
    )
    def test_a_denial_is_still_raised(
        self, ada: Any, note: Note, refused: Any, raised: Any
    ) -> None:
        with pytest.raises(raised):
            from_a_row(ReturnsToTheList.as_view(spec=raising(refused)), ada, note.pk)

    @pytest.mark.parametrize(
        "query", ["", "?next=https://evil.example/"], ids=["no-next", "unsafe-next"]
    )
    def test_without_a_safe_next_it_returns_to_the_success_url(
        self, ada: Any, note: Note, query: str
    ) -> None:
        view = ReturnsToTheList.as_view(spec=archive_spec(affordances=[PAUSED]))
        response, request = from_a_row(view, ada, note.pk, query)
        assert response.url == "/done/"
        assert len(stored(request)) == 1

    def test_a_page_naming_no_field_returns_to_the_success_url(self, ada: Any, note: Note) -> None:
        view = ReturnsToTheList.as_view(
            spec=archive_spec(affordances=[PAUSED]), redirect_field_name=None
        )
        response, _ = from_a_row(view, ada, note.pk)
        assert response.url == "/done/"

    def test_without_the_messages_middleware_it_is_the_page_again(
        self, ada: Any, note: Note
    ) -> None:
        # No store on the request: Django raises MessageFailure, and the page
        # renders the refusal itself rather than lose it in a redirect.
        view = ReturnsToTheList.as_view(spec=archive_spec(affordances=[PAUSED]))
        request = signed_in(
            FACTORY.post(f"/notes/{note.pk}/archive/{FROM_THE_LIST}", data=PRESSED), ada
        )
        response = view(request, pk=note.pk)
        assert response.status_code == 409
        assert (response.context_data["errors"], response.context_data["code"]) == (
            ["Archiving is paused."],
            "paused",
        )

    def test_off_by_default_the_refusal_is_the_page_again(self, ada: Any, note: Note) -> None:
        view = ReturnsToTheList.as_view(
            spec=archive_spec(affordances=[PAUSED]), refusal_redirect=False
        )
        response, request = from_a_row(view, ada, note.pk)
        assert response.status_code == 409
        assert stored(request) == []
        # The page posts back to itself, and carries the way back to the list.
        assert response.context_data["next"] == "/list/?page=3"

    def test_get_refusal_url_may_be_overridden(self, ada: Any, note: Note) -> None:
        class ToTheNote(ReturnsToTheList):
            def get_refusal_url(self, refused: Any) -> str:
                return f"/notes/{self.kwargs['pk']}/?refused={refused.code}"

        view = ToTheNote.as_view(spec=archive_spec(affordances=[PAUSED]))
        response, _ = from_a_row(view, ada, note.pk)
        assert response.url == f"/notes/{note.pk}/?refused=paused"


class TestARefusalRedirectNeedsSomewhereToGo:
    MISSING = (
        "Refusing.refusal_redirect needs a success_url, where a refused post returns without "
        "a next, or a get_refusal_url that returns one."
    )

    @staticmethod
    def refusing(**attributes: Any) -> Any:
        # ``get_success_url`` overridden, so the base's own check of
        # ``success_url`` passes and the one answering is the refusal's.
        attributes = {
            "template_name": PAGE,
            "route_kwargs": ("pk",),
            "refusal_redirect": True,
            "get_success_url": lambda self, result: "/done/",
            **attributes,
        }
        return type("Refusing", (SpecConfirmView,), attributes).as_view(spec=archive_spec())

    def test_no_success_url_and_no_get_refusal_url_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as raised:
            self.refusing()
        assert str(raised.value) == self.MISSING
        assert "needs a success_url, where a successful post" not in str(raised.value)

    def test_a_success_url_is_somewhere_to_go(self) -> None:
        self.refusing(success_url="/done/")

    def test_an_overridden_get_refusal_url_is_somewhere_to_go(self) -> None:
        self.refusing(get_refusal_url=lambda self, refused: "/list/")

    def test_get_refusal_url_may_be_passed_to_as_view(self) -> None:
        view = type("Refusing", (SpecConfirmView,), {"template_name": PAGE})
        view.as_view(
            spec=archive_spec(),
            route_kwargs=("pk",),
            refusal_redirect=True,
            get_success_url=lambda result: "/done/",
            get_refusal_url=lambda refused: "/list/",
        )

    def test_a_page_not_redirecting_needs_none(self) -> None:
        self.refusing(refusal_redirect=False)


class TestTheRedirectFieldName:
    def test_the_commit_button_s_name_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="redirect_field_name and commit_button"):
            ReturnsToTheList.as_view(spec=archive_spec(), commit_button="next")
