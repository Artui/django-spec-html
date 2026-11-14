from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest
from django import forms
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django_service_specs import (
    DataclassValidator,
    FormValidator,
    Parameter,
    Parameters,
    ServerArguments,
    ServiceSpec,
    ValidationContext,
    Validator,
)

from django_spec_html.views.spec_form_view import SpecFormView
from tests.dispatch.utils import OPEN, Record, Titled, make_user
from tests.dispatch_app.models import Tag
from tests.http.utils import FACTORY, signed_in

pytestmark = pytest.mark.django_db


@dataclasses.dataclass
class Book:
    title: str
    pages: int | None = None


class BookForm(forms.Form):
    title = forms.CharField()
    pages = forms.IntegerField(required=False)


class TitleForm(forms.Form):
    title = forms.CharField()


def _never(value: Any) -> None:
    raise ValidationError("the form's own validator ran")


class GuardedBookForm(forms.Form):
    title = forms.CharField(validators=[_never])
    pages = forms.IntegerField(required=False)


class Declared(Validator):
    """Declares ``declared`` and hands the arguments back; ``unsendable`` as given."""

    def __init__(self, *declared: Parameter, unsendable: frozenset[str] = frozenset()) -> None:
        self.declared = Parameters.of(*declared)
        self._unsendable = unsendable

    @property
    def unsendable(self) -> frozenset[str]:
        return self._unsendable

    def parameters(self) -> Parameters:
        return self.declared

    def validate(self, arguments: Mapping[str, Any], context: ValidationContext) -> dict[str, Any]:
        return dict(arguments)


class Shelved:
    """A validated object read by attribute, as a pydantic model is."""

    def __init__(self, title: str) -> None:
        self.title = title


class ShelvedValidator(Declared):
    def validated(self, arguments: Mapping[str, Any], context: ValidationContext) -> Any:
        validated = super().validated(arguments, context)
        return dataclasses.replace(validated, data=Shelved(arguments["title"]))


def book_spec(validator: Any = None, **kwargs: Any) -> ServiceSpec:
    return ServiceSpec(
        service=Record(),
        validator=DataclassValidator(Book) if validator is None else validator,
        permissions=OPEN,
        **kwargs,
    )


def page(spec: ServiceSpec, form_class: Any = BookForm, **initkwargs: Any) -> Any:
    return SpecFormView.as_view(
        spec=spec,
        form_class=form_class,
        template_name="spec_form.html",
        success_url="/done/",
        **initkwargs,
    )


def get(view: Any, user: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.get("/"), user), **url_kwargs)


def post(view: Any, user: Any, data: Any, **url_kwargs: Any) -> Any:
    return view(signed_in(FACTORY.post("/", data=data), user), **url_kwargs)


def refusal(spec: ServiceSpec, form_class: Any, **initkwargs: Any) -> str:
    with pytest.raises(ImproperlyConfigured) as refused:
        page(spec, form_class, **initkwargs)
    return str(refused.value)


@pytest.fixture(autouse=True)
def urlconf(settings: Any) -> None:
    settings.ROOT_URLCONF = __name__


urlpatterns: list[Any] = []


@pytest.fixture
def ada() -> Any:
    return make_user("ada")


class TestAFormClassOverADataclassValidator:
    def test_get_renders_the_form_class(self, ada: Any) -> None:
        response = get(page(book_spec()), ada)
        assert response.status_code == 200
        assert type(response.context_data["form"]) is BookForm

    def test_a_valid_post_dispatches_and_redirects(self, ada: Any) -> None:
        spec = book_spec()
        response = post(page(spec), ada, {"title": "Dune", "pages": "412"})
        assert (response.status_code, response["Location"]) == (302, "/done/")
        assert spec.service.calls[0]["data"] == Book(title="Dune", pages=412)

    def test_a_refused_post_carries_the_validator_s_errors_alone(self, ada: Any) -> None:
        response = post(page(book_spec(), GuardedBookForm), ada, {"title": "Dune", "pages": "x"})
        assert response.status_code == 400
        assert set(response.context_data["form"].errors) == {"pages"}

    def test_the_form_s_own_validators_never_run_on_a_success(self, ada: Any) -> None:
        response = post(page(book_spec(), GuardedBookForm), ada, {"title": "Dune"})
        assert response.status_code == 302

    def test_a_refresh_validates_through_the_validator_only(self, ada: Any) -> None:
        view = page(book_spec(), GuardedBookForm, commit_button="save", validate_on_refresh=True)
        response = post(view, ada, {"title": "Dune", "pages": "x"})
        errors = response.context_data["form"].errors
        assert (response.status_code, set(errors)) == (200, {"pages"})


@dataclasses.dataclass
class TagRow:
    key: str
    count: int


class TagForm(forms.ModelForm):
    count = forms.IntegerField()

    class Meta:
        model = Tag
        fields = ("key",)


@override_settings(ROOT_URLCONF=__name__)
class TestAModelFormClassRunsNoQuery(TestCase):
    def setUp(self) -> None:
        self.ada = make_user("ada")
        Tag.objects.create(key="red")
        self.spec = book_spec(DataclassValidator(TagRow))

    def test_a_refused_post_asks_no_uniqueness(self) -> None:
        view = page(self.spec, TagForm)
        with self.assertNumQueries(0):
            response = post(view, self.ada, {"key": "red", "count": "x"})
        assert response.status_code == 400
        assert set(response.context_data["form"].errors) == {"count"}

    def test_a_success_asks_no_uniqueness(self) -> None:
        view = page(self.spec, TagForm)
        with CaptureQueriesContext(connection) as queries:
            response = post(view, self.ada, {"key": "red", "count": "3"})
        assert response.status_code == 302
        assert [q["sql"] for q in queries if "SAVEPOINT" not in q["sql"]] == []


class TestAFormClassOverAnObjectValidator:
    def test_success_message_reads_the_object_s_attributes(self, ada: Any) -> None:
        spec = book_spec(ShelvedValidator(Parameter("title", "string", required=True)))
        request = signed_in(FACTORY.post("/", data={"title": "Dune"}), ada)
        request.session = {}
        request._messages = FallbackStorage(request)
        response = page(spec, TitleForm, success_message="%(title)s shelved")(request)
        assert response.status_code == 302
        assert [str(m) for m in request._messages] == ["Dune shelved"]


class TestSuccessMessage:
    def test_a_named_placeholder_formats_over_a_dataclass(self, ada: Any) -> None:
        request = signed_in(FACTORY.post("/", data={"title": "Dune"}), ada)
        request.session = {}
        request._messages = FallbackStorage(request)
        response = page(book_spec(), success_message="%(title)s added")(request)
        assert response.status_code == 302
        assert [str(m) for m in request._messages] == ["Dune added"]

    def test_a_placeholder_the_object_lacks_is_a_key_error(self, ada: Any) -> None:
        request = signed_in(FACTORY.post("/", data={"title": "Dune"}), ada)
        request.session = {}
        request._messages = FallbackStorage(request)
        with pytest.raises(KeyError, match="isbn"):
            page(book_spec(), success_message="%(isbn)s added")(request)

    def test_a_mapping_s_data_formats_as_before(self, ada: Any) -> None:
        spec = book_spec(Declared(Parameter("title", "string", required=True)))
        request = signed_in(FACTORY.post("/", data={"title": "Dune"}), ada)
        request.session = {}
        request._messages = FallbackStorage(request)
        response = page(spec, TitleForm, success_message="%(title)s kept")(request)
        assert response.status_code == 302
        assert [str(m) for m in request._messages] == ["Dune kept"]


class KeywordForm(forms.Form):
    title = forms.CharField()
    pages = forms.IntegerField(required=False)

    def __init__(self, *args: Any, shelf: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.shelf = shelf


class Shelving(SpecFormView):
    def get_form_kwargs(self) -> Mapping[str, Any]:
        return {"shelf": "top"}


def shelving(spec: ServiceSpec, **initkwargs: Any) -> Any:
    return Shelving.as_view(
        spec=spec,
        form_class=KeywordForm,
        template_name="spec_form.html",
        success_url="/done/",
        **initkwargs,
    )


class TestGetFormKwargs:
    def test_the_default_is_empty(self) -> None:
        assert SpecFormView().get_form_kwargs() == {}

    def test_get_builds_the_form_with_them(self, ada: Any) -> None:
        assert get(shelving(book_spec()), ada).context_data["form"].shelf == "top"

    def test_a_refused_post_builds_the_form_with_them(self, ada: Any) -> None:
        response = post(shelving(book_spec()), ada, {"title": ""})
        assert (response.status_code, response.context_data["form"].shelf) == (400, "top")

    @pytest.mark.parametrize("key", ["data", "files", "instance"])
    def test_a_keyword_the_page_sets_is_refused(self, ada: Any, key: str) -> None:
        class Overreaching(SpecFormView):
            def get_form_kwargs(self) -> Mapping[str, Any]:
                return {key: None}

        view = Overreaching.as_view(
            spec=book_spec(), form_class=BookForm, template_name="spec_form.html", success_url="/d/"
        )
        with pytest.raises(ImproperlyConfigured) as refused:
            get(view, ada)
        assert str(refused.value) == (
            f"Overreaching.get_form_kwargs() returned {key!r}, which the page sets itself: "
            "it returns the form's other keywords."
        )


class SplitForm(forms.Form):
    title = forms.CharField()
    when = forms.SplitDateTimeField()


class ExtraForm(BookForm):
    subtitle = forms.CharField(required=False)


class FileForm(forms.Form):
    title = forms.CharField()
    cover = forms.FileField(required=False)


class FloatPagesForm(forms.Form):
    title = forms.CharField()
    pages = forms.FloatField(required=False)


class OptionalTitleForm(forms.Form):
    title = forms.CharField(required=False)
    pages = forms.IntegerField(required=False)


class RequiredPagesForm(forms.Form):
    title = forms.CharField()
    pages = forms.IntegerField()


class DateForm(forms.Form):
    when = forms.DateField()


class TagsForm(forms.Form):
    tags = forms.MultipleChoiceField(choices=[("1", "1")])


class CleanForm(BookForm):
    def clean(self) -> dict[str, Any]:
        return super().clean()


class CleanFieldForm(BookForm):
    def clean_title(self) -> str:
        return self.cleaned_data["title"]


@dataclasses.dataclass
class Catalogued:
    title: str
    isbn: str


@dataclasses.dataclass
class CataloguedBook:
    title: str
    isbn: str
    pages: int | None = None


class TestAsViewRefuses:
    def test_a_form_the_wire_read_cannot_carry(self) -> None:
        message = refusal(book_spec(), SplitForm)
        assert message.startswith("SpecFormView.form_class SplitForm cannot be read off a post: ")
        assert "when" in message

    def test_a_field_the_spec_does_not_declare(self) -> None:
        assert refusal(book_spec(), ExtraForm) == (
            "SpecFormView.form_class ExtraForm posts 'subtitle', which the spec does not "
            "declare: it would reach dispatch as an unknown argument."
        )

    def test_a_clear_checkbox_the_spec_does_not_declare(self) -> None:
        spec = book_spec(
            Declared(Parameter("title", "string", required=True), Parameter("cover", "file"))
        )
        assert refusal(spec, FileForm) == (
            "SpecFormView.form_class FileForm posts 'cover-clear', which the spec does not "
            "declare: it would reach dispatch as an unknown argument."
        )

    def test_a_required_parameter_nothing_fills(self) -> None:
        spec = book_spec(DataclassValidator(Catalogued))
        assert refusal(spec, TitleForm, route_kwargs=()) == (
            "SpecFormView.form_class TitleForm does not post 'isbn', which the spec requires, "
            "and neither the server nor the route fills it."
        )

    def test_a_required_parameter_the_route_may_fill_passes(self) -> None:
        page(book_spec(DataclassValidator(Catalogued)), TitleForm)
        page(book_spec(DataclassValidator(Catalogued)), TitleForm, route_kwargs=("isbn",))
        page(book_spec(DataclassValidator(Catalogued)), TitleForm, route_kwargs={"code": "isbn"})

    def test_an_optional_parameter_the_form_leaves_out_passes(self) -> None:
        page(book_spec(), TitleForm, route_kwargs=())

    def test_a_spec_with_no_validator_declares_no_field(self) -> None:
        spec = ServiceSpec(service=Record(), permissions=OPEN)
        assert refusal(spec, TitleForm) == (
            "SpecFormView.form_class TitleForm posts 'title', which the spec does not declare: "
            "it would reach dispatch as an unknown argument."
        )

    def test_a_required_parameter_the_server_fills_passes(self) -> None:
        page(
            book_spec(DataclassValidator(Catalogued)),
            TitleForm,
            route_kwargs=(),
            server_arguments=ServerArguments({"isbn": "x"}),
        )

    def test_a_type_disagreement(self) -> None:
        assert refusal(book_spec(), FloatPagesForm) == (
            "SpecFormView.form_class FloatPagesForm posts 'pages' as number, and the spec "
            "declares integer."
        )

    def test_a_format_disagreement(self) -> None:
        spec = book_spec(Declared(Parameter("when", "string", required=True, format="date-time")))
        assert refusal(spec, DateForm) == (
            "SpecFormView.form_class DateForm posts 'when' as string (date), and the spec "
            "declares string (date-time)."
        )

    def test_an_element_type_disagreement(self) -> None:
        spec = book_spec(Declared(Parameter("tags", "array", required=True, items="integer")))
        assert refusal(spec, TagsForm) == (
            "SpecFormView.form_class TagsForm posts 'tags' as array of string, and the spec "
            "declares array of integer."
        )

    def test_a_validator_s_any_takes_any_field(self) -> None:
        page(book_spec(Declared(Parameter("when", "any", required=True))), DateForm)

    def test_a_format_only_the_form_declares_passes(self) -> None:
        page(book_spec(Declared(Parameter("when", "string", required=True))), DateForm)

    def test_a_required_field_over_an_optional_parameter(self) -> None:
        assert refusal(book_spec(), RequiredPagesForm) == (
            "SpecFormView.form_class RequiredPagesForm requires 'pages', and the spec does not: "
            "the form and the spec must agree on whether it may be left out."
        )

    def test_an_optional_field_over_a_required_parameter(self) -> None:
        assert refusal(book_spec(), OptionalTitleForm) == (
            "SpecFormView.form_class OptionalTitleForm does not require 'title', and the spec "
            "does: the form and the spec must agree on whether it may be left out."
        )

    def test_an_unsendable_validator(self) -> None:
        spec = book_spec(
            Declared(Parameter("title", "string", required=True), unsendable=frozenset({"code"}))
        )
        assert refusal(spec, TitleForm) == (
            "SpecFormView's spec requires 'code', which its Validator cannot declare, so no "
            "form can post it."
        )

    def test_a_form_class_over_a_form_validator(self) -> None:
        assert refusal(book_spec(FormValidator(BookForm)), BookForm) == (
            "SpecFormView sets form_class over a FormValidator, whose form is already the "
            "page's: drop form_class."
        )

    @pytest.mark.parametrize(
        ("form_class", "method"), [(CleanForm, "clean"), (CleanFieldForm, "clean_title")]
    )
    def test_a_form_class_with_its_own_clean(self, form_class: Any, method: str) -> None:
        assert refusal(book_spec(), form_class) == (
            f"SpecFormView.form_class {form_class.__name__} defines {method}(), which the page "
            "never runs: the rule belongs in the spec's Validator."
        )

    def test_a_model_form_s_own_clean_is_django_s(self) -> None:
        page(book_spec(DataclassValidator(TagRow)), TagForm)

    def test_a_commit_button_named_like_a_field_of_the_form_class(self) -> None:
        message = refusal(book_spec(), BookForm, commit_button="title")
        assert message.startswith("SpecFormView.commit_button names 'title', a field BookForm")

    def test_a_spec_with_no_form_names_form_class(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecFormView.as_view(spec=book_spec(Titled()), success_url="/done/")
        assert "form_class" in str(refused.value)


class TestActions:
    def test_every_action_shares_the_form_class(self, ada: Any) -> None:
        save, draft = book_spec(), book_spec()
        view = SpecFormView.as_view(
            actions={"save": save, "draft": draft},
            form_class=BookForm,
            template_name="spec_form.html",
            success_url="/done/",
        )
        assert type(get(view, ada).context_data["form"]) is BookForm
        assert post(view, ada, {"title": "Dune", "draft": ""}).status_code == 302
        assert (len(save.service.calls), len(draft.service.calls)) == (0, 1)

    def test_each_action_is_checked(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecFormView.as_view(
                actions={
                    "save": book_spec(),
                    "draft": book_spec(DataclassValidator(CataloguedBook)),
                },
                form_class=BookForm,
                success_url="/done/",
                route_kwargs=(),
            )
        assert str(refused.value).startswith(
            "SpecFormView.actions['draft'].form_class BookForm does not post 'isbn'"
        )

    def test_an_action_named_like_a_field_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            SpecFormView.as_view(
                actions={"title": book_spec()}, form_class=BookForm, success_url="/done/"
            )
        assert str(refused.value) == (
            "SpecFormView.actions names 'title', a field BookForm declares: a field is posted by "
            "every submit, so it cannot say which action runs."
        )


def test_form_class_is_none_by_default() -> None:
    assert SpecFormView.form_class is None
