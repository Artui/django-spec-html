from __future__ import annotations

from typing import Any

import pytest
from django import forms
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.core.exceptions import BadRequest, ImproperlyConfigured
from django.http import HttpResponse
from django.urls import path
from django_service_specs import (
    DispatchResult,
    FormValidator,
    Parameter,
    Parameters,
    ServiceConflict,
    ServiceSpec,
)

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import OPEN, Record, make_user
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

urlpatterns = [path("done/", lambda request: HttpResponse(), name="done")]

HTMX = {"HTTP_HX_REQUEST": "true"}


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


class Conflicted:
    def __call__(self, **pool: Any) -> Any:
        raise ServiceConflict("Already published.")


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


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


class TestAFormPostRunsTheActionItNames:
    def test_a_post_carrying_save_dispatches_save(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        response = form_view(save, publish)(request_for({"title": "Hi", "save": ""}))
        assert (response.status_code, response.url) == (302, "/done/")
        assert (len(calls(save)), calls(publish)) == (1, [])

    def test_a_post_carrying_publish_dispatches_publish(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        form_view(save, publish)(request_for({"title": "Hi", "publish": "1"}))
        assert (calls(save), len(calls(publish))) == ([], 1)

    def test_a_post_naming_no_action_is_a_bad_request(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        with pytest.raises(BadRequest, match=r"names none of the page's actions: save, publish"):
            form_view(save, publish)(request_for({"title": "Hi"}))
        assert calls(save) == calls(publish) == []

    def test_a_post_naming_two_actions_is_a_bad_request(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        with pytest.raises(BadRequest, match=r"names more than one action: save, publish"):
            form_view(save, publish)(request_for({"title": "Hi", "save": "", "publish": ""}))
        assert calls(save) == calls(publish) == []

    def test_an_htmx_post_naming_none_is_a_bad_request_too(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        with pytest.raises(BadRequest):
            form_view(save, publish)(request_for({"title": "Hi"}, **HTMX))

    def test_get_renders_the_first_action_s_spec(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        response = form_view(publish, save)(request_for())
        assert response.context_data["spec"] is publish
        assert response.context_data["view"].action_name is None

    def test_a_refused_post_re_renders_with_the_action_that_ran(self, save: ServiceSpec) -> None:
        conflicted = form_spec(service=Conflicted())
        response = form_view(save, conflicted)(request_for({"title": "Hi", "publish": ""}))
        assert response.status_code == 409
        assert response.context_data["spec"] is conflicted
        assert response.context_data["form"].non_field_errors() == ["Already published."]

    def test_a_post_that_does_not_commit_names_no_action(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        view = form_view(save, publish, commit_button="commit")
        response = view(request_for({"title": "Hi"}))
        assert (response.status_code, response.context_data["refreshed"]) == (200, True)
        assert response.context_data["spec"] is save
        assert calls(save) == calls(publish) == []

    def test_under_a_commit_button_the_action_is_chosen_beside_it(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        view = form_view(save, publish, commit_button="commit")
        view(request_for({"title": "Hi", "commit": "", "publish": ""}))
        assert (calls(save), len(calls(publish))) == ([], 1)


class Told(SpecFormView):
    success_url = "/done/"
    success_message = "unused"

    def get_success_url(self, result: DispatchResult) -> str:
        return f"/done/{self.action_name}/"

    def get_success_message(self, result: DispatchResult) -> str:
        return f"Ran {self.action_name}."


class TestTheSuccessHooksKnowTheAction:
    def test_get_success_url_reads_action_name(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        view = Told.as_view(actions={"save": save, "publish": publish})
        request = request_for({"title": "Hi", "publish": ""})
        response = view(request)
        assert response.url == "/done/publish/"
        assert [str(each) for each in get_messages(request)] == ["Ran publish."]

    def test_a_page_without_actions_has_none(self, save: ServiceSpec) -> None:
        response = Told.as_view(spec=save)(request_for({"title": "Hi"}))
        assert response.url == "/done/None/"

    def test_success_url_and_message_stay_page_wide(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        view = form_view(save, publish, success_message="Saved %(title)s.")
        request = request_for({"title": "Hi", "publish": ""})
        assert view(request).url == "/done/"
        assert [str(each) for each in get_messages(request)] == ["Saved Hi."]


class ToldConfirm(SpecConfirmView):
    success_url = "/done/"

    def get_success_url(self, result: DispatchResult) -> str:
        return f"/done/{self.action_name}/"


class TestAConfirmationRunsTheActionItNames:
    def test_each_button_dispatches_its_own_spec(self) -> None:
        archive, delete = confirm_spec(), confirm_spec()
        view = ToldConfirm.as_view(actions={"archive": archive, "delete": delete})
        response = view(request_for({"delete": ""}))
        assert response.url == "/done/delete/"
        assert (calls(archive), len(calls(delete))) == ([], 1)

    def test_a_post_naming_none_is_a_bad_request(self) -> None:
        view = ToldConfirm.as_view(actions={"archive": confirm_spec()})
        with pytest.raises(BadRequest):
            view(request_for({}))

    def test_get_renders_the_first_action_s_spec(self) -> None:
        archive = confirm_spec()
        view = ToldConfirm.as_view(actions={"archive": archive, "delete": confirm_spec()})
        assert view(request_for()).context_data["spec"] is archive


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

    def test_a_commit_button_that_is_an_action(
        self, save: ServiceSpec, publish: ServiceSpec
    ) -> None:
        message = refused(
            SpecFormView, actions={"save": save, "publish": publish}, commit_button="publish"
        )
        assert message == (
            "SpecFormView.commit_button names 'publish', one of its actions: the button that "
            "commits is one press, and an action's is another."
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

    def test_actions_on_a_page_that_serves_none(self, save: ServiceSpec) -> None:
        message = refused(SpecFormSetView, actions={"save": save})
        assert message == (
            "SpecFormSetView takes no actions: only SpecFormView and SpecConfirmView "
            "dispatch one spec per submit button."
        )
