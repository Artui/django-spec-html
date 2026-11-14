from __future__ import annotations

from typing import Any

import pytest
from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy
from django_service_specs import (
    Affordance,
    FormValidator,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
)

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import OPEN, PK, Record, make_user, note_by_pk
from tests.dispatch_app.models import Note
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db

TAGS = [("red", "Red"), ("green", "Green"), ("blue", "Blue")]


class TagsForm(forms.Form):
    tags = forms.MultipleChoiceField(choices=TAGS)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.picked = self.data.getlist("tags")


class SeesInstance(forms.Form):
    title = forms.CharField(required=False)

    def __init__(self, *args: Any, instance: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen = instance


class SeesInstanceByKeyword(forms.Form):
    title = forms.CharField(required=False)

    def __init__(self, *args: Any, seen: Any = None, instance: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seen = instance


class CatchesAll(forms.Form):
    title = forms.CharField(required=False)

    def __init__(self, *args: Any, **instance: Any) -> None:
        super().__init__(*args, **instance)


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


def post(view: Any, user: Any, data: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user), **url_kwargs)


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


urlpatterns: list[Any] = []


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


@pytest.fixture
def note(ada: Any) -> Note:
    return Note.objects.create(owner=ada, title="One")


class TestAFormReadingGetlist:
    """The kernel binds a QueryDict, so a form written for HTML validates the post."""

    def test_the_post_succeeds_with_every_value(self, ada: Any) -> None:
        spec = spec_of(TagsForm)
        response = post(page(spec), ada, {"tags": ["red", "blue"]})
        assert response.status_code == 302
        assert spec.service.calls[0]["data"] == {"tags": ["red", "blue"]}

    def test_a_refused_post_renders_the_form_with_what_was_posted(self, ada: Any) -> None:
        response = post(page(spec_of(TagsForm)), ada, {"tags": ["red", "plum"]})
        assert response.status_code == 400
        assert response.context_data["form"].picked == ["red", "plum"]

    def test_the_get_builds_the_form(self, ada: Any) -> None:
        response = get(page(spec_of(TagsForm)), ada)
        assert (response.status_code, response.context_data["form"].picked) == (200, [])


class TestAPlainFormDeclaringInstance:
    """The page builds the form as the kernel's bind does: the target, or ``None``."""

    @pytest.mark.parametrize("form_class", [SeesInstance, SeesInstanceByKeyword])
    def test_the_get_hands_it_the_target(self, ada: Any, note: Note, form_class: Any) -> None:
        response = get(page(spec_of(form_class, row=True)), ada, pk=str(note.pk))
        form = response.context_data["form"]
        assert (type(form.seen), form.seen.pk, form.initial["title"]) == (Note, note.pk, "One")

    def test_a_refused_post_hands_it_the_target(self, ada: Any, note: Note) -> None:
        refusing = Affordance(code="closed", reason="Closed.", when=lambda: False)
        spec = spec_of(SeesInstance, row=True, affordances=[refusing])
        response = post(page(spec), ada, {"title": "Two"}, pk=str(note.pk))
        assert response.status_code == 409
        assert response.context_data["form"].seen.pk == note.pk

    def test_a_create_hands_it_none(self, ada: Any) -> None:
        response = get(page(spec_of(SeesInstance)), ada)
        assert response.context_data["form"].seen is None

    def test_a_catch_all_named_instance_is_handed_nothing(self, ada: Any, note: Note) -> None:
        response = get(page(spec_of(CatchesAll, row=True)), ada, pk=str(note.pk))
        assert response.status_code == 200
        posted = post(page(spec_of(CatchesAll, row=True)), ada, {"title": "T"}, pk=str(note.pk))
        assert posted.status_code == 302


def untitled() -> Affordance:
    return Affordance(
        code="titled",
        reason=gettext_lazy("Only an untitled note is edited here."),
        when=Q(title=""),
    )


class TestALazyReasonOnTheRow:
    """A row condition's lazy reason reaches the template as text."""

    def test_the_form_page_hands_out_text(self, ada: Any, note: Note) -> None:
        spec = spec_of(SeesInstance, row=True, affordances=[untitled()])
        unavailable = get(page(spec), ada, pk=str(note.pk)).context_data["unavailable"]
        assert type(unavailable.reason) is str
        assert (unavailable.code, unavailable.reason) == (
            "titled",
            "Only an untitled note is edited here.",
        )

    def test_the_confirm_page_hands_out_text(self, ada: Any, note: Note) -> None:
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
        unavailable = get(view, ada, pk=str(note.pk)).context_data["unavailable"]
        assert type(unavailable.reason) is str
