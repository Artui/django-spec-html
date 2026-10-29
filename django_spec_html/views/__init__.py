"""A spec served as a server-rendered page: a write as its form, a read as its rows."""

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.spec_selector_view import SpecSelectorView

__all__ = ["SpecConfirmView", "SpecFormSetView", "SpecFormView", "SpecSelectorView"]
