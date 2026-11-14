from __future__ import annotations

import dataclasses
import datetime
import json
from collections.abc import Iterator, Mapping
from typing import Annotated, Any

import django
import pytest
from django import forms
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db.models import Q, QuerySet
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseRedirect,
    StreamingHttpResponse,
)
from django.urls import path
from django.utils.translation import gettext_lazy
from django_service_specs import (
    Affordance,
    DataclassPresenter,
    DispatchResult,
    FieldMarking,
    FilePresenter,
    FormValidator,
    Parameter,
    Parameters,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.spec_selector_view import SpecSelectorView
from django_spec_html.views.utils import Served, SpecPageBase
from tests.dispatch.utils import (
    OPEN,
    PK,
    TENANT_SEEDS,
    OwnerOnly,
    Record,
    Refuse,
    make_user,
    note_by_pk,
)
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, note_spec, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


class _NoPageRender(SpecPageBase):
    """A page supplying every hook but ``_render_page``, as one that forgot it would be."""

    @classmethod
    def _check_spec(cls, label: str, spec: ServiceSpec) -> None: ...

    @classmethod
    def _check_page(cls, label: str, spec: ServiceSpec, initkwargs: Mapping[str, Any]) -> None: ...

    def _form(
        self, spec: ServiceSpec, principal: Any, target: Any, served: Served, **bound: Any
    ) -> Any: ...

    def _arguments(
        self, form: Any, spec: ServiceSpec, url_kwargs: Mapping[str, Any], served: Served
    ) -> Mapping[str, Any]:
        return {}

    def _render_refused(
        self, form: Any, refused: Any, target: Any, /, **context: Any
    ) -> HttpResponse:
        return HttpResponse()


class TestTheHooks:
    def test_a_page_missing_a_hook_is_refused_at_as_view(self) -> None:
        with pytest.raises(ImproperlyConfigured, match=r"^_NoPageRender .*: _render_page\.$"):
            _NoPageRender.as_view(spec=note_spec(), success_url="/done/")

    def test_the_base_itself_is_refused_naming_every_hook(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecPageBase.as_view(spec=note_spec(), success_url="/done/")
        hooks = "_arguments, _check_page, _check_spec, _form, _render_page, _render_refused"
        assert str(refused.value) == f"SpecPageBase does not supply the page's hooks: {hooks}."


class PostForm(forms.Form):
    title = forms.CharField()


class OtherForm(forms.Form):
    title = forms.CharField()


def form_spec(form_class: type[forms.Form] = PostForm, service: Any = None) -> ServiceSpec:
    return ServiceSpec(
        service=Record() if service is None else service,
        permissions=OPEN,
        validator=FormValidator(form_class),
    )


def confirm_spec(*reads: Parameter) -> ServiceSpec:
    return ServiceSpec(service=Record(), permissions=OPEN, reads=Parameters.of(*reads))


def calls(spec: ServiceSpec) -> list[dict[str, Any]]:
    return spec.service.calls


def request_for(data: Any = None, **extra: Any) -> Any:
    raw = FACTORY.get("/", **extra) if data is None else FACTORY.post("/", data=data, **extra)
    request = signed_in(raw, make_user("ada"))
    request._messages = CookieStorage(request)  # ty: ignore[unresolved-attribute]
    return request


@pytest.fixture
def save() -> ServiceSpec:
    return form_spec()


@pytest.fixture
def publish() -> ServiceSpec:
    return form_spec()


def form_view(save: ServiceSpec, publish: ServiceSpec, **initkwargs: Any) -> Any:
    return SpecFormView.as_view(
        actions={"save": save, "publish": publish}, success_url="/done/", **initkwargs
    )


class TestTheShippedTemplatesRenderOneButtonPerAction:
    def test_the_form_page(self, save: ServiceSpec, publish: ServiceSpec) -> None:
        html = form_view(save, publish)(request_for()).render().content.decode()
        assert '<button type="submit" name="save">Save</button>' in html
        assert '<button type="submit" name="publish">Publish</button>' in html
        assert html.index('name="save"') < html.index('name="publish"')
        assert html.count("<button") == 2

    def test_the_confirmation_page(self) -> None:
        view = SpecConfirmView.as_view(
            actions={"archive": confirm_spec(), "delete": confirm_spec()}, success_url="/done/"
        )
        html = view(request_for()).render().content.decode()
        assert '<button type="submit" name="archive">Archive</button>' in html
        assert '<button type="submit" name="delete">Delete</button>' in html
        assert html.count("<button") == 2


TITLED = Affordance(code="titled", reason="Only an untitled note is published.", when=Q(title=""))


PAUSED = Affordance(code="paused", reason="Archiving is paused.", when=lambda: False)


CLOSED = Affordance(code="closed", reason="Saving is closed.", when=lambda: False)


def row_spec(*affordances: Affordance) -> ServiceSpec:
    return ServiceSpec(
        service=Record(),
        permissions=OPEN,
        validator=FormValidator(PostForm),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK
        ),
        affordances=list(affordances),
    )


def untitled_notes(*, pk: int) -> Any:
    return Note.objects.filter(title="")


def paused_spec(*affordances: Affordance) -> ServiceSpec:
    return ServiceSpec(service=Record(), permissions=OPEN, affordances=list(affordances))


@pytest.fixture
def note() -> Note:
    return Note.objects.create(owner=make_user("bo"), title="Titled")


def on_note(view: Any, note: Note, data: Any = None) -> Any:
    return view(request_for(data), pk=str(note.pk))


def rendered(response: Any) -> str:
    return response.render().content.decode()


def note_actions(publish: ServiceSpec, save: ServiceSpec) -> Any:
    return SpecFormView.as_view(actions={"publish": publish, "save": save}, success_url="/done/")


def confirm_actions(**actions: ServiceSpec) -> Any:
    return SpecConfirmView.as_view(actions=actions, success_url="/done/")


class TestEachActionAnswersItsOwnAvailability:
    def test_the_form_page_disables_only_the_unavailable_action(self, note: Note) -> None:
        response = on_note(note_actions(row_spec(TITLED), row_spec()), note)
        html = rendered(response)
        assert "<fieldset>" in html
        assert '<button type="submit" name="publish" disabled>Publish</button>' in html
        assert '<button type="submit" name="save">Save</button>' in html
        assert 'data-action="save"' not in html
        assert response.context_data["unavailable_actions"] == {"publish": TITLED}
        assert "unavailable" not in response.context_data

    def test_the_disabled_action_shows_its_reason_once(self, note: Note) -> None:
        html = rendered(on_note(note_actions(row_spec(TITLED), row_spec()), note))
        paragraph = (
            '<p data-action="publish" data-code="titled">Only an untitled note is published.</p>'
        )
        assert paragraph in html
        assert html.count(TITLED.reason) == 1

    def test_the_buttons_follow_the_actions_order(self, note: Note) -> None:
        response = on_note(note_actions(row_spec(TITLED), row_spec()), note)
        assert response.context_data["action_buttons"] == [("publish", TITLED), ("save", None)]

    def test_an_untitled_note_leaves_every_action_available(self, note: Note) -> None:
        note.title = ""
        note.save()
        response = on_note(note_actions(row_spec(TITLED), row_spec()), note)
        assert "unavailable_actions" not in response.context_data
        assert response.context_data["action_buttons"] == [("publish", None), ("save", None)]
        assert " disabled" not in rendered(response)

    def test_every_action_is_asked_of_the_row_the_page_shows(self, note: Note) -> None:
        Note.objects.create(owner=note.owner, title="")
        elsewhere = dataclasses.replace(
            row_spec(TITLED),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=untitled_notes, reads=PK
            ),
        )
        view = SpecFormView.as_view(
            actions={"save": row_spec(), "publish": elsewhere}, success_url="/done/"
        )
        assert on_note(view, note).context_data["unavailable_actions"] == {"publish": TITLED}

    def test_a_post_running_the_available_action_succeeds(self, note: Note) -> None:
        publish, save = row_spec(TITLED), row_spec()
        response = on_note(note_actions(publish, save), note, {"title": "Hi", "save": ""})
        assert (response.status_code, response.url) == (302, "/done/")
        assert (calls(publish), len(calls(save))) == ([], 1)

    def test_a_post_naming_the_unavailable_action_is_refused(self, note: Note) -> None:
        publish, save = row_spec(TITLED), row_spec()
        response = on_note(note_actions(publish, save), note, {"title": "Hi", "publish": ""})
        assert response.status_code == 409
        assert response.context_data["code"] == "titled"
        assert calls(publish) == calls(save) == []

    def test_the_confirm_page_disables_only_the_unavailable_action(self) -> None:
        response = confirm_actions(archive=paused_spec(PAUSED), delete=paused_spec())(request_for())
        assert response.context_data["unavailable_actions"] == {"archive": PAUSED}
        assert "unavailable" not in response.context_data
        html = rendered(response)
        assert '<button type="submit" name="archive" disabled>Archive</button>' in html
        assert '<button type="submit" name="delete">Delete</button>' in html
        assert 'data-action="delete"' not in html
        assert '<p data-action="archive" data-code="paused">Archiving is paused.</p>' in html
        assert html.count(PAUSED.reason) == 1

    def test_a_confirmation_naming_the_unavailable_action_is_refused(self) -> None:
        archive, delete = paused_spec(PAUSED), paused_spec()
        response = confirm_actions(archive=archive, delete=delete)(request_for({"archive": ""}))
        assert response.status_code == 409
        assert calls(archive) == calls(delete) == []


class TestEveryActionUnavailable:
    def test_the_form_page_disables_the_form(self, note: Note) -> None:
        response = on_note(note_actions(row_spec(TITLED), row_spec(CLOSED)), note)
        context = response.context_data
        assert context["unavailable"] is TITLED
        assert context["unavailable_actions"] == {"publish": TITLED, "save": CLOSED}
        html = rendered(response)
        assert "<fieldset disabled>" in html
        assert '<button type="submit" name="save" disabled>Save</button>' in html
        assert (html.count(TITLED.reason), html.count(CLOSED.reason)) == (1, 1)

    def test_the_confirm_page_disables_every_button(self) -> None:
        view = confirm_actions(archive=paused_spec(PAUSED), delete=paused_spec(CLOSED))
        response = view(request_for())
        assert response.context_data["unavailable"] is PAUSED
        html = rendered(response)
        assert html.count(" disabled>") == 2
        assert (html.count(PAUSED.reason), html.count(CLOSED.reason)) == (1, 1)

    def test_an_action_keyed_items_renders(self, note: Note) -> None:
        view = SpecFormView.as_view(
            actions={"items": row_spec(TITLED), "save": row_spec()}, success_url="/done/"
        )
        html = rendered(on_note(view, note))
        assert '<button type="submit" name="items" disabled>Items</button>' in html
        assert '<p data-action="items" data-code="titled">' in html


class TestASingleSpecPageIsUnchanged:
    def test_the_form_page(self, note: Note) -> None:
        view = SpecFormView.as_view(spec=row_spec(TITLED), success_url="/done/")
        response = on_note(view, note)
        context = response.context_data
        assert context["unavailable"] is TITLED
        assert "unavailable_actions" not in context
        assert "action_buttons" not in context
        assert "refused_actions" not in context
        html = rendered(response)
        assert '<p data-code="titled">Only an untitled note is published.</p>' in html
        assert "<fieldset disabled>" in html

    def test_the_confirm_page(self) -> None:
        view = SpecConfirmView.as_view(spec=paused_spec(PAUSED), success_url="/done/")
        response = view(request_for())
        assert "unavailable_actions" not in response.context_data
        assert "refused_actions" not in response.context_data
        html = rendered(response)
        assert '<p data-code="paused">Archiving is paused.</p>' in html
        assert '<button type="submit" disabled>Confirm</button>' in html

    def test_an_available_confirmation_gives_no_reason(self) -> None:
        view = SpecConfirmView.as_view(spec=paused_spec(), success_url="/done/")
        html = rendered(view(request_for()))
        assert "<p data-code" not in html
        assert '<button type="submit">Confirm</button>' in html


class SignedIn(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return bool(principal.is_authenticated)


class NoScope(PermissionCheck):
    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any) -> bool:
        return False


class TenantOfAda(PermissionCheck):
    """Admits ada's tenant alone, at class and object level, recording each tenant it is offered."""

    def __init__(self) -> None:
        self.tenants: list[str] = []

    def has_permission(self, principal: Any, spec: Any, tenant: str) -> bool:
        self.tenants.append(tenant)
        return tenant == "tenant-of-ada"

    def has_object_permission(self, principal: Any, spec: Any, target: Any, tenant: str) -> bool:
        self.tenants.append(tenant)
        return tenant == "tenant-of-ada"


class SetRefused(PermissionCheck):
    """Admits every principal, then refuses every set, recording each set it is offered."""

    def __init__(self) -> None:
        self.scopes: list[Any] = []

    def has_permission(self, principal: Any, spec: Any) -> bool:
        return True

    def has_scope_permission(self, principal: Any, spec: Any, scope: Any) -> bool:
        self.scopes.append(scope)
        return False


def on_a_set(selector: Any, *permissions: Any) -> ServiceSpec:
    return ServiceSpec(
        service=Record(),
        permissions=permissions or OPEN,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector),
    )


def guarded(*permissions: Any, affordances: tuple[Affordance, ...] = ()) -> ServiceSpec:
    return dataclasses.replace(row_spec(*affordances), permissions=permissions)


def guarded_confirm(*permissions: Any) -> ServiceSpec:
    return ServiceSpec(service=Record(), permissions=permissions)


def save_then(publish: ServiceSpec, **initkwargs: Any) -> Any:
    return SpecFormView.as_view(
        actions={"save": row_spec(), "publish": publish}, success_url="/done/", **initkwargs
    )


def capped_publish() -> ServiceSpec:
    capped = Parameters.of(Parameter("pk", "integer", required=True, maximum=0))
    return dataclasses.replace(
        row_spec(),
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=capped
        ),
    )


class TestARefusedActionIsDisabledWithNoReason:
    def test_the_form_page(self, note: Note) -> None:
        response = on_note(save_then(guarded(Refuse())), note)
        context = response.context_data
        assert context.get("refused_actions") == frozenset({"publish"})
        assert "unavailable_actions" not in context
        assert "unavailable" not in context
        assert context["action_buttons"] == [("save", None), ("publish", None)]
        html = rendered(response)
        assert '<button type="submit" name="publish" disabled>Publish</button>' in html
        assert '<button type="submit" name="save">Save</button>' in html
        assert "<fieldset>" in html
        assert "data-action" not in html
        assert Refuse.message not in html

    def test_its_object_check_is_asked_of_the_row_the_page_shows(self, ada: Any) -> None:
        shown = Note.objects.create(owner=make_user("bo"), title="Titled")
        Note.objects.create(owner=ada, title="")
        check = OwnerOnly()
        elsewhere = dataclasses.replace(
            guarded(check),
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=untitled_notes, reads=PK
            ),
        )
        response = get(save_then(elsewhere), ada, pk=str(shown.pk))
        assert response.context_data.get("refused_actions") == frozenset({"publish"})
        assert check.rows == [shown]
        assert '<button type="submit" name="publish" disabled>' in rendered(response)

    def test_the_owner_s_row_leaves_it_enabled(self, ada: Any) -> None:
        own = Note.objects.create(owner=ada, title="Titled")
        check = OwnerOnly()
        response = get(save_then(guarded(check)), ada, pk=str(own.pk))
        assert "refused_actions" not in response.context_data
        assert check.rows == [own]
        assert " disabled" not in rendered(response)

    def test_the_first_action_is_asked_once(self, ada: Any) -> None:
        own = Note.objects.create(owner=ada, title="Titled")
        check = OwnerOnly()
        view = SpecFormView.as_view(
            actions={"publish": guarded(check), "save": row_spec()}, success_url="/done/"
        )
        get(view, ada, pk=str(own.pk))
        assert check.rows == [own]

    def test_a_check_reading_a_seed_is_offered_the_page_s_registry(self, ada: Any) -> None:
        own = Note.objects.create(owner=ada, title="Titled")
        check = TenantOfAda()
        view = save_then(guarded(check), pool_seeds=TENANT_SEEDS)
        response = get(view, ada, pk=str(own.pk))
        assert "refused_actions" not in response.context_data
        assert check.tenants == ["tenant-of-ada", "tenant-of-ada"]

    def test_a_refused_action_s_affordances_are_not_asked(self, note: Note) -> None:
        response = on_note(save_then(guarded(Refuse(), affordances=(CLOSED,))), note)
        context = response.context_data
        assert context.get("refused_actions") == frozenset({"publish"})
        assert "unavailable_actions" not in context
        assert context["action_buttons"] == [("save", None), ("publish", None)]
        html = rendered(response)
        assert CLOSED.reason not in html
        assert "data-code" not in html

    def test_the_first_unavailable_and_the_rest_refused_leave_the_fields(self, note: Note) -> None:
        view = SpecFormView.as_view(
            actions={"publish": row_spec(TITLED), "save": guarded(Refuse())}, success_url="/done/"
        )
        response = on_note(view, note)
        context = response.context_data
        assert "unavailable" not in context
        assert context["unavailable_actions"] == {"publish": TITLED}
        assert context.get("refused_actions") == frozenset({"save"})
        html = rendered(response)
        assert "<fieldset>" in html
        assert html.count(" disabled>") == 2
        assert html.count(TITLED.reason) == 1

    @pytest.mark.parametrize("check", [Refuse(), NoScope()], ids=["class", "scope"])
    def test_the_confirm_page(self, check: Any) -> None:
        view = confirm_actions(archive=paused_spec(), delete=guarded_confirm(check))
        response = view(request_for())
        assert response.context_data.get("refused_actions") == frozenset({"delete"})
        html = rendered(response)
        assert '<button type="submit" name="delete" disabled>Delete</button>' in html
        assert '<button type="submit" name="archive">Archive</button>' in html
        assert "data-action" not in html

    def test_an_anonymous_visitor_is_refused_what_a_signed_in_one_is_not(self, ada: Any) -> None:
        view = confirm_actions(archive=paused_spec(), delete=guarded_confirm(SignedIn()))
        anonymous = view(signed_in(FACTORY.get("/")))
        signed = view(signed_in(FACTORY.get("/"), ada))
        assert anonymous.context_data.get("refused_actions") == frozenset({"delete"})
        assert "refused_actions" not in signed.context_data
        assert '<button type="submit" name="delete" disabled>' in rendered(anonymous)
        assert '<button type="submit" name="delete">' in rendered(signed)

    def test_a_route_capture_its_own_parameters_refuse(self, note: Note) -> None:
        response = on_note(save_then(capped_publish()), note)
        assert response.context_data.get("refused_actions") == {"publish"}

    def test_with_route_refusal_404_off_a_capture_is_left_to_its_post(self, note: Note) -> None:
        view = save_then(capped_publish(), route_refusal_404=False)
        assert "refused_actions" not in on_note(view, note).context_data

    def test_its_scope_check_is_asked_of_the_set_the_page_shows(self, note: Note) -> None:
        Note.objects.create(owner=note.owner, title="")
        check = SetRefused()
        delete = on_a_set(lambda: Note.objects.filter(title=""), check)
        view = confirm_actions(archive=on_a_set(lambda: Note.objects.all()), delete=delete)
        response = view(request_for())
        assert response.context_data.get("refused_actions") == {"delete"}
        assert [sorted(each.pk for each in scope) for scope in check.scopes] == [
            sorted(Note.objects.values_list("pk", flat=True))
        ]
        assert '<button type="submit" name="delete" disabled>Delete</button>' in rendered(response)

    def test_the_first_action_refused_still_refuses_the_page(self, note: Note) -> None:
        view = SpecFormView.as_view(
            actions={"publish": guarded(Refuse()), "save": row_spec()}, success_url="/done/"
        )
        with pytest.raises(PermissionDenied):
            on_note(view, note)


class TestAPostToARefusedActionIsRefused:
    def test_on_the_form_page(self, note: Note) -> None:
        save, publish = row_spec(), guarded(Refuse())
        view = SpecFormView.as_view(actions={"save": save, "publish": publish}, success_url="/")
        with pytest.raises(PermissionDenied):
            on_note(view, note, {"title": "Hi", "publish": ""})
        assert calls(save) == calls(publish) == []

    def test_on_the_form_page_by_its_object_check(self, note: Note) -> None:
        save, publish = row_spec(), guarded(OwnerOnly())
        view = SpecFormView.as_view(actions={"save": save, "publish": publish}, success_url="/")
        with pytest.raises(PermissionDenied):
            on_note(view, note, {"title": "Hi", "publish": ""})
        assert calls(save) == calls(publish) == []

    def test_on_the_form_page_by_its_route_capture(self, note: Note) -> None:
        with pytest.raises(Http404):
            on_note(save_then(capped_publish()), note, {"title": "Hi", "publish": ""})

    def test_on_the_confirm_page_by_its_scope_check(self) -> None:
        archive, delete = paused_spec(), guarded_confirm(NoScope())
        with pytest.raises(PermissionDenied):
            confirm_actions(archive=archive, delete=delete)(request_for({"delete": ""}))
        assert calls(archive) == calls(delete) == []


def refused(view_class: type[Any], **initkwargs: Any) -> str:
    with pytest.raises(ImproperlyConfigured) as raised:
        view_class.as_view(success_url="/done/", **initkwargs)
    return str(raised.value)


class TestAsViewRefuses:
    def test_actions_beside_a_spec(self, save: ServiceSpec) -> None:
        message = refused(SpecFormView, spec=save, actions={"save": save})
        assert message == (
            "SpecFormView sets both spec and actions: a page serves one spec, or one per "
            "action, never both."
        )

    def test_actions_beside_a_class_level_spec(self, save: ServiceSpec) -> None:
        class Specced(SpecFormView):
            spec = save

        assert "sets both spec and actions" in refused(Specced, actions={"save": save})

    def test_actions_that_are_not_a_mapping(self, save: ServiceSpec) -> None:
        message = refused(SpecFormView, actions=[save])
        assert message.startswith("SpecFormView.actions must be a non-empty mapping")

    def test_empty_actions(self) -> None:
        assert "non-empty mapping" in refused(SpecFormView, actions={})

    @pytest.mark.parametrize("key", ["", 3])
    def test_a_key_that_is_not_a_button_s_name(self, save: ServiceSpec, key: Any) -> None:
        message = refused(SpecFormView, actions={key: save})
        assert message == (
            f"SpecFormView.actions keys are the names of the submit buttons that run them, "
            f"non-empty strings; got {key!r}."
        )

    def test_an_action_that_is_not_a_service_spec(self) -> None:
        message = refused(SpecFormView, actions={"save": object()})
        assert message.startswith("SpecFormView.actions['save'].as_view() needs a ServiceSpec")

    def test_form_actions_whose_form_classes_differ(self, save: ServiceSpec) -> None:
        message = refused(SpecFormView, actions={"save": save, "other": form_spec(OtherForm)})
        assert message == (
            "SpecFormView.actions['other'] validates with OtherForm, and the page's form is "
            "PostForm, the first action's: every action posts the page's one form."
        )

    def test_a_form_action_whose_validator_is_no_form(self, save: ServiceSpec) -> None:
        message = refused(SpecFormView, actions={"save": save, "archive": confirm_spec()})
        assert message.startswith("SpecFormView.actions['archive'] renders the form")

    def test_a_confirm_action_with_a_validator(self, save: ServiceSpec) -> None:
        message = refused(SpecConfirmView, actions={"archive": confirm_spec(), "save": save})
        assert message.startswith(
            "SpecConfirmView.actions['save'] serves a spec with no Validator, and this one "
            "validates with PostForm"
        )

    def test_a_confirm_action_demanding_what_nothing_supplies(self) -> None:
        demanding = confirm_spec(Parameter("reason", "string", required=True))
        message = refused(SpecConfirmView, actions={"archive": confirm_spec(), "x": demanding})
        assert message.startswith("SpecConfirmView.actions['x'] requires reason")

    @pytest.mark.parametrize("button", ["commit", "publish"])
    def test_a_commit_button_beside_actions(
        self, save: ServiceSpec, publish: ServiceSpec, button: str
    ) -> None:
        message = refused(
            SpecFormView, actions={"save": save, "publish": publish}, commit_button=button
        )
        assert message == (
            "SpecFormView sets both actions and commit_button: an actions page commits only "
            "through an action's button, and a post naming none answers 400."
        )

    def test_a_confirm_page_with_a_commit_button_beside_actions(self) -> None:
        message = refused(
            SpecConfirmView,
            actions={"archive": confirm_spec(), "delete": confirm_spec()},
            commit_button="commit",
        )
        assert message == (
            "SpecConfirmView sets both actions and commit_button: an actions page commits only "
            "through an action's button, and a post naming none answers 400."
        )

    def test_a_redirect_field_that_is_an_action(self, save: ServiceSpec) -> None:
        message = refused(SpecFormView, actions={"save": save}, redirect_field_name="save")
        assert message == (
            "SpecFormView.redirect_field_name names 'save', one of its actions: the URL to "
            "return to is posted by every submit, so every post would name that action."
        )

    def test_an_action_named_like_a_field_of_the_form(self, save: ServiceSpec) -> None:
        message = refused(SpecFormView, actions={"title": save})
        assert message == (
            "SpecFormView.actions names 'title', a field PostForm declares: a field is posted "
            "by every submit, so it cannot say which action runs."
        )

    def test_a_confirm_action_named_like_a_posted_argument(self) -> None:
        ids = Parameter("ids", "array", items="integer")
        message = refused(
            SpecConfirmView,
            actions={"ids": confirm_spec(ids), "reject": confirm_spec(ids)},
            posted_arguments=("ids",),
        )
        assert message == (
            "SpecConfirmView.actions names 'ids', which every post can carry as "
            "posted_arguments or the CSRF token: it cannot say which action runs."
        )

    def test_a_confirm_action_named_like_the_csrf_token(self) -> None:
        message = refused(
            SpecConfirmView,
            actions={"csrfmiddlewaretoken": confirm_spec(), "reject": confirm_spec()},
        )
        assert message.startswith("SpecConfirmView.actions names 'csrfmiddlewaretoken'")

    def test_actions_on_a_page_that_serves_none(self, save: ServiceSpec) -> None:
        message = refused(SpecFormSetView, actions={"save": save})
        assert message == (
            "SpecFormSetView takes no actions: only SpecFormView and SpecConfirmView "
            "dispatch one spec per submit button."
        )


HTMX = {"HTTP_HX_REQUEST": "true"}


class TitleForm(forms.Form):
    title = forms.CharField()


def as_text(title: str) -> bytes:
    return title.encode()


def chunked(title: str) -> Iterator[bytes]:
    yield title.encode()
    yield b"!"


TEXT = FilePresenter("text/plain", filename="minutes.txt", render=as_text)


def file_spec(presenter: Any = TEXT, returns: Any = "Minutes", **fields: Any) -> ServiceSpec:
    fields.setdefault("validator", FormValidator(TitleForm))
    return ServiceSpec(service=Record(returns), permissions=OPEN, presenter=presenter, **fields)


def posted(view: Any, headers: Any = None) -> tuple[Any, Any]:
    request = signed_in(FACTORY.post("/", {"title": "Hi"}, **(headers or {})), make_user("ada"))
    request._messages = CookieStorage(request)
    return view(request), request


def body(response: Any) -> bytes:
    try:
        return b"".join(response.streaming_content)
    finally:
        response.close()


def file_post_form_view(spec: ServiceSpec, **initkwargs: Any) -> Any:
    initkwargs = {"success_url": "/done/", "success_message": "Saved.", **initkwargs}
    return SpecFormView.as_view(spec=spec, **initkwargs)


class TestAPostOfAFileSpec:
    def test_answers_the_file_as_an_attachment_with_no_redirect_and_no_message(self) -> None:
        response, request = posted(file_post_form_view(file_spec()))
        assert isinstance(response, FileResponse)
        assert response["Content-Disposition"] == 'attachment; filename="minutes.txt"'
        assert response["Content-Type"] == "text/plain"
        assert not response.has_header("Location")
        assert body(response) == b"Minutes"
        assert list(get_messages(request)) == []

    def test_an_htmx_post_answers_the_file_with_no_hx_header(self) -> None:
        response, _ = posted(file_post_form_view(file_spec()), HTMX)
        assert isinstance(response, FileResponse)
        assert [name for name in response.headers if name.startswith("HX-")] == []
        assert body(response) == b"Minutes"

    def test_as_attachment_false_answers_it_inline(self) -> None:
        response, _ = posted(file_post_form_view(file_spec(), as_attachment=False))
        assert response["Content-Disposition"] == 'inline; filename="minutes.txt"'
        response.close()

    def test_an_iterable_of_bytes_is_streamed(self) -> None:
        presenter = FilePresenter("text/plain", filename="minutes.txt", render=chunked)
        response, _ = posted(file_post_form_view(file_spec(presenter)))
        assert type(response) is StreamingHttpResponse
        assert response["Content-Disposition"] == 'attachment; filename="minutes.txt"'
        assert body(response) == b"Minutes!"

    def test_a_none_result_redirects_with_the_message_as_any_success(self) -> None:
        response, request = posted(file_post_form_view(file_spec(returns=None, allow_none=True)))
        assert isinstance(response, HttpResponseRedirect)
        assert response.url == "/done/"
        assert [str(each) for each in get_messages(request)] == ["Saved."]

    def test_a_spec_presenting_no_file_still_redirects_with_its_message(self) -> None:
        response, request = posted(file_post_form_view(file_spec(presenter=None)))
        assert isinstance(response, HttpResponseRedirect)
        assert [str(each) for each in get_messages(request)] == ["Saved."]

    def test_the_confirmation_page_answers_the_file_too(self) -> None:
        view = SpecConfirmView.as_view(spec=file_spec(validator=None), success_url="/done/")
        response, _ = posted(view)
        assert isinstance(response, FileResponse)
        assert body(response) == b"Minutes"


def page_html(view: Any) -> str:
    response = view(signed_in(FACTORY.get("/"), make_user("ada")))
    return response.render().content.decode()


NO_BOOST = 'hx-boost="false"'


class TestTheShippedForm:
    def test_a_file_spec_s_form_is_posted_natively(self) -> None:
        assert NO_BOOST in page_html(file_post_form_view(file_spec()))

    def test_a_non_file_spec_s_form_is_left_to_the_page(self) -> None:
        html = page_html(file_post_form_view(file_spec(presenter=None)))
        assert "<form" in html
        assert "hx-boost" not in html

    def test_an_actions_page_with_any_file_action_is_posted_natively(self) -> None:
        actions = {"save": file_spec(presenter=None), "export": file_spec()}
        view = SpecFormView.as_view(actions=actions, success_url="/done/")
        assert NO_BOOST in page_html(view)

    def test_an_actions_page_with_no_file_action_is_left_to_the_page(self) -> None:
        actions = {"save": file_spec(presenter=None), "keep": file_spec(presenter=None)}
        view = SpecFormView.as_view(actions=actions, success_url="/done/")
        assert "hx-boost" not in page_html(view)

    def test_a_file_spec_s_confirmation_is_posted_natively(self) -> None:
        view = SpecConfirmView.as_view(spec=file_spec(validator=None), success_url="/done/")
        assert NO_BOOST in page_html(view)

    def test_a_non_file_confirmation_is_left_to_the_page(self) -> None:
        spec = file_spec(presenter=None, validator=None)
        view = SpecConfirmView.as_view(spec=spec, success_url="/done/")
        assert "hx-boost" not in page_html(view)


class SeesInstance(forms.Form):
    title = forms.CharField(required=False)

    def __init__(self, *args: Any, instance: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen = instance


def spec_of(form_class: type[forms.Form], *, row: bool = False, **kwargs: Any) -> ServiceSpec:
    if row:
        kwargs["instance_selector_spec"] = SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK
        )
    return ServiceSpec(
        service=Record(), validator=FormValidator(form_class), permissions=OPEN, **kwargs
    )


def page(spec: ServiceSpec) -> Any:
    return SpecFormView.as_view(spec=spec, template_name="spec_form.html", success_url="/done/")


def get(view: Any, user: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user), **url_kwargs)


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def binding_note(ada: Any) -> Note:
    return Note.objects.create(owner=ada, title="One")


def untitled() -> Affordance:
    return Affordance(
        code="titled",
        reason=gettext_lazy("Only an untitled note is edited here."),
        when=Q(title=""),
    )


class TestALazyReasonOnTheRow:
    """A row condition's lazy reason reaches the template as text."""

    def test_the_form_page_hands_out_text(self, ada: Any, binding_note: Note) -> None:
        spec = spec_of(SeesInstance, row=True, affordances=[untitled()])
        unavailable = get(page(spec), ada, pk=str(binding_note.pk)).context_data["unavailable"]
        assert type(unavailable.reason) is str
        assert (unavailable.code, unavailable.reason) == (
            "titled",
            "Only an untitled note is edited here.",
        )

    def test_the_confirm_page_hands_out_text(self, ada: Any, binding_note: Note) -> None:
        spec = ServiceSpec(
            service=Record(),
            permissions=OPEN,
            affordances=[untitled()],
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=note_by_pk, reads=PK
            ),
        )
        view = SpecConfirmView.as_view(
            spec=spec, template_name="spec_confirm.html", success_url="/", route_kwargs={"pk": "pk"}
        )
        unavailable = get(view, ada, pk=str(binding_note.pk)).context_data["unavailable"]
        assert type(unavailable.reason) is str


INVALID = "Select a valid choice. sideways is not one of the available choices."


TOO_LONG = "Ensure this value has at most 5 characters (it has 7)."


DIRECTION = Parameter("direction", "string", required=True, choices=("up", "down"))


class Reorder(forms.Form):
    direction = forms.ChoiceField(choices=[("up", "Up"), ("down", "Down")], required=False)
    title = forms.CharField(max_length=5, required=False)


class Row(forms.Form):
    title = forms.CharField(max_length=5)


@dataclasses.dataclass
class NoteOut:
    pk: Annotated[int, FieldMarking.handle()]
    title: Annotated[str, FieldMarking.label()]


def notes(*, direction: str) -> QuerySet[Note]:
    return Note.objects.order_by("pk" if direction == "up" else "-pk")


def route_form_view(**initkwargs: Any) -> Any:
    spec = ServiceSpec(service=Record(), validator=FormValidator(Reorder), permissions=OPEN)
    return SpecFormView.as_view(
        spec=spec, template_name="spec_form.html", success_url="/done/", **initkwargs
    )


def confirm_view(**initkwargs: Any) -> Any:
    spec = ServiceSpec(service=Record(), permissions=OPEN, reads=Parameters.of(DIRECTION))
    initkwargs.setdefault("route_kwargs", ("direction",))
    return SpecConfirmView.as_view(
        spec=spec, template_name="spec_confirm_view.html", success_url="/done/", **initkwargs
    )


def formset_view(**initkwargs: Any) -> Any:
    spec = ServiceSpec(
        service=Record(),
        validator=FormValidator(Row),
        permissions=OPEN,
        many=True,
        reads=Parameters.of(DIRECTION),
    )
    return SpecFormSetView.as_view(
        spec=spec, template_name="spec_formset.html", success_url="/done/", **initkwargs
    )


def selector_view(**initkwargs: Any) -> Any:
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=notes,
        presenter=DataclassPresenter(NoteOut),
        permissions=OPEN,
        reads=Parameters.of(DIRECTION),
    )
    return SpecSelectorView.as_view(spec=spec, **initkwargs)


def rows(*titles: str) -> dict[str, Any]:
    data = {
        "form-TOTAL_FORMS": str(len(titles)),
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }
    data.update({f"form-{index}-title": title for index, title in enumerate(titles)})
    return data


def route_get(view: Any, user: Any, direction: str) -> Any:
    return view(signed_in(FACTORY.get("/"), user), direction=direction)


def route_posted(data: Mapping[str, Any], user: Any) -> Any:
    request = signed_in(FACTORY.post("/", data=data), user)
    request._messages = CookieStorage(request)  # ty: ignore[unresolved-attribute]
    return request


def post(view: Any, user: Any, data: Mapping[str, Any], direction: str) -> Any:
    return view(route_posted(data, user), direction=direction)


PAGES = {
    "form": (route_form_view, {"title": "Hi"}),
    "confirm": (confirm_view, {}),
    "confirm-redirect": (lambda **off: confirm_view(refusal_redirect=True, **off), {}),
    "formset": (formset_view, rows("Hi")),
}


class TestARefusedRouteCaptureIsNotFound:
    """A capture naming nothing is a 404, as a URL converter mismatch would be."""

    @pytest.mark.parametrize("page", list(PAGES))
    def test_on_get(self, ada: Any, page: str) -> None:
        with pytest.raises(Http404):
            route_get(PAGES[page][0](), ada, "sideways")

    @pytest.mark.parametrize("page", list(PAGES))
    def test_on_post_before_anything_runs(self, ada: Any, page: str) -> None:
        view, data = PAGES[page]
        with pytest.raises(Http404):
            post(view(), ada, data, "sideways")

    def test_on_the_selector_page(self, ada: Any) -> None:
        with pytest.raises(Http404):
            route_get(selector_view(), ada, "sideways")

    @pytest.mark.parametrize("page", list(PAGES))
    def test_a_capture_it_accepts_serves_the_page(self, ada: Any, page: str) -> None:
        assert route_get(PAGES[page][0](), ada, "up").status_code == 200

    def test_a_capture_the_selector_accepts_serves_the_page(self, ada: Any) -> None:
        response = route_get(selector_view(), ada, "down")
        assert response.status_code == 200


class TestARefusedPostedArgumentIsUnchanged:
    """Only the route's refusal moved: a refused post answers as it did, either way."""

    @pytest.mark.parametrize("not_found", [True, False])
    def test_on_the_form_page(self, ada: Any, not_found: bool) -> None:
        response = post(
            route_form_view(route_refusal_404=not_found), ada, {"title": "toolong"}, "up"
        )
        errors = response.context_data["form"].errors
        assert (response.status_code, errors) == (400, {"title": [TOO_LONG]})

    @pytest.mark.parametrize("not_found", [True, False])
    def test_on_the_formset_page(self, ada: Any, not_found: bool) -> None:
        response = post(formset_view(route_refusal_404=not_found), ada, rows("toolong"), "up")
        errors = response.context_data["formset"].errors
        assert (response.status_code, errors) == (400, [{"title": [TOO_LONG]}])


class TestTurnedOff:
    """``route_refusal_404 = False`` restores 0.12.0's answers: a 400 on GET, a refusal on POST."""

    @pytest.mark.parametrize("page", list(PAGES))
    def test_get_renders_the_page_a_choice_it_never_checked(self, ada: Any, page: str) -> None:
        assert (
            route_get(PAGES[page][0](route_refusal_404=False), ada, "sideways").status_code == 200
        )

    def test_a_form_post_is_refused_on_its_form(self, ada: Any) -> None:
        response = post(route_form_view(route_refusal_404=False), ada, {"title": "Hi"}, "sideways")
        errors = response.context_data["form"].errors
        assert (response.status_code, errors) == (400, {"direction": [INVALID]})

    def test_a_confirm_post_is_refused_on_its_page(self, ada: Any) -> None:
        response = post(confirm_view(route_refusal_404=False), ada, {}, "sideways")
        assert (response.status_code, response.context_data["errors"]) == (
            400,
            [f"direction: {INVALID}"],
        )

    def test_a_confirm_post_with_refusal_redirect_returns_with_the_message(self, ada: Any) -> None:
        view = confirm_view(route_refusal_404=False, refusal_redirect=True)
        request = route_posted({}, ada)
        response = view(request, direction="sideways")
        assert (response.status_code, response.url) == (302, "/done/")
        assert [each.message for each in get_messages(request)] == [f"direction: {INVALID}"]

    def test_a_formset_post_is_refused_on_its_formset(self, ada: Any) -> None:
        response = post(formset_view(route_refusal_404=False), ada, rows("Hi"), "sideways")
        errors = list(response.context_data["formset"].non_form_errors())
        assert (response.status_code, errors) == (400, [f"direction: {INVALID}"])

    def test_the_selector_page_is_refused_at_400(self, ada: Any) -> None:
        response = route_get(selector_view(route_refusal_404=False), ada, "sideways")
        assert (response.status_code, response.context_data["errors"]) == (
            400,
            [f"direction: {INVALID}"],
        )


VARY = "HX-Request, HX-Boosted, HX-History-Restore-Request"


def save_spec(returns: Any = 7) -> ServiceSpec:
    return ServiceSpec(
        service=Record(returns), permissions=OPEN, validator=FormValidator(TitleForm)
    )


def trigger_page(base: type = SpecFormView, **attributes: Any) -> type:
    attributes = {
        "template_name": "page.html",
        "fragment_template_name": "fragment.html",
        "success_url": "/done/",
        **attributes,
    }
    return type("Saves", (base,), attributes)


def trigger_posted(cls: type, headers: Any = HTMX, spec: ServiceSpec | None = None) -> Any:
    request = signed_in(FACTORY.post("/", data={"title": "Hi"}, **headers), make_user("ada"))
    response = cls.as_view(spec=save_spec() if spec is None else spec)(request)
    return response.render() if hasattr(response, "render") else response


def hx_headers(response: Any) -> dict[str, str]:
    return {name: value for name, value in response.headers.items() if name.startswith("HX-")}


def templates_with(settings: Any, *, result: bool) -> None:
    form = '<form hx-post="">{{ form }}</form>'
    shown = "<p>Saved {{ result.value }}.</p>"
    if not result:
        found = {"fragment.html": form}
    elif django.VERSION >= (6, 0):
        found = {
            "fragment.html": (f"{form}{{% partialdef spec-result %}}{shown}{{% endpartialdef %}}")
        }
    else:
        found = {"fragment.html": form, "fragment.html#spec-result": shown}
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [("django.template.loaders.locmem.Loader", {"page.html": "", **found})]
            },
        }
    ]


@pytest.fixture
def result_fragment(settings: Any) -> None:
    templates_with(settings, result=True)


@pytest.fixture
def no_result_fragment(settings: Any) -> None:
    templates_with(settings, result=False)


@pytest.mark.usefixtures("result_fragment")
class TestTheTrigger:
    def test_the_default_answer_is_the_one_0_12_0_sent(self) -> None:
        response = trigger_posted(trigger_page())
        assert (response.status_code, response.content) == (200, b"<p>Saved 7.</p>")
        assert dict(response.headers) == {
            "Content-Type": "text/html; charset=utf-8",
            "HX-Trigger": "spec-succeeded",
            "Vary": VARY,
        }

    def test_a_name_is_sent_as_it_is(self) -> None:
        assert hx_headers(trigger_posted(trigger_page(success_trigger="saved"))) == {
            "HX-Trigger": "saved"
        }

    def test_a_mapping_is_sent_as_the_json_htmx_reads(self) -> None:
        trigger = {"saved": {"title": "Café", "on": datetime.date(2026, 11, 14)}, "closed": None}
        sent = trigger_posted(trigger_page(success_trigger=trigger))["HX-Trigger"]
        assert sent.startswith("{")
        assert sent.isascii()
        assert json.loads(sent) == {"saved": {"title": "Café", "on": "2026-11-14"}, "closed": None}

    def test_none_sends_no_trigger(self) -> None:
        response = trigger_posted(trigger_page(success_trigger=None))
        assert (response.status_code, hx_headers(response)) == (200, {})
        assert response.content == b"<p>Saved 7.</p>"

    @pytest.mark.parametrize(
        ("timing", "header"),
        [
            ("receive", "HX-Trigger"),
            ("swap", "HX-Trigger-After-Swap"),
            ("settle", "HX-Trigger-After-Settle"),
        ],
    )
    def test_each_timing_names_its_header(self, timing: str, header: str) -> None:
        response = trigger_posted(
            trigger_page(success_trigger="saved", success_trigger_timing=timing)
        )
        assert hx_headers(response) == {header: "saved"}

    def test_get_success_trigger_reads_the_result(self) -> None:
        class Named(trigger_page()):
            def get_success_trigger(self, result: DispatchResult) -> Any:
                return {"saved": {"id": result.value}}

        assert json.loads(trigger_posted(Named)["HX-Trigger"]) == {"saved": {"id": 7}}

    def test_a_payload_json_cannot_carry_is_refused_when_sent(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="Saves's success trigger .* JSON"):
            trigger_posted(trigger_page(success_trigger={"saved": object()}))

    def test_a_confirmation_sends_its_trigger_too(self) -> None:
        spec = ServiceSpec(service=Record(7), permissions=OPEN)
        response = trigger_posted(
            trigger_page(SpecConfirmView, success_trigger="confirmed"), spec=spec
        )
        assert hx_headers(response) == {"HX-Trigger": "confirmed"}

    def test_a_post_without_htmx_still_redirects_alone(self) -> None:
        cls = trigger_page(success_trigger={"saved": 1}, success_trigger_timing="swap")
        response = trigger_posted(cls, headers={})
        assert (response.status_code, response["Location"], hx_headers(response)) == (
            302,
            "/done/",
            {},
        )


class TestTheRefresh:
    @pytest.mark.usefixtures("result_fragment")
    def test_a_post_without_htmx_still_redirects(self) -> None:
        response = trigger_posted(trigger_page(success_refresh=True), headers={})
        assert (response.status_code, hx_headers(response)) == (302, {})

    @pytest.mark.usefixtures("no_result_fragment")
    def test_a_refresh_needs_no_fragment(self) -> None:
        response = trigger_posted(trigger_page(success_refresh=True, success_trigger=None))
        assert (response.status_code, response.content) == (200, b"")
        assert hx_headers(response) == {"HX-Refresh": "true"}
        assert response["Vary"] == VARY

    @pytest.mark.usefixtures("result_fragment")
    def test_a_refresh_carries_the_trigger_htmx_fires_before_it_reloads(self) -> None:
        response = trigger_posted(trigger_page(success_refresh=True))
        assert response.content == b""
        assert hx_headers(response) == {"HX-Trigger": "spec-succeeded", "HX-Refresh": "true"}


@pytest.mark.usefixtures("result_fragment")
class TestCheckedAtAsView:
    @pytest.mark.parametrize(
        ("attributes", "message"),
        [
            ({"success_trigger": ""}, "success_trigger must be"),
            ({"success_trigger": 3}, "success_trigger must be"),
            ({"success_trigger": {}}, "success_trigger must be"),
            ({"success_trigger": {"": 1}}, "success_trigger must be"),
            ({"success_trigger": {1: 1}}, "success_trigger must be"),
            ({"success_trigger_timing": "load"}, "success_trigger_timing must be"),
            ({"success_refresh": 1}, "success_refresh must be"),
            ({"success_refresh": True, "success_trigger_timing": "swap"}, "never fires"),
            ({"success_refresh": True, "success_trigger_timing": "settle"}, "never fires"),
        ],
    )
    def test_a_value_the_page_cannot_send_is_refused(
        self, attributes: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=message):
            trigger_page(**attributes).as_view(spec=save_spec())

    def test_initkwargs_are_checked_too(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="success_trigger_timing must be"):
            trigger_page().as_view(spec=save_spec(), success_trigger_timing="load")

    def test_a_refresh_without_a_trigger_takes_any_timing(self) -> None:
        trigger_page(
            success_refresh=True, success_trigger=None, success_trigger_timing="swap"
        ).as_view(spec=save_spec())
