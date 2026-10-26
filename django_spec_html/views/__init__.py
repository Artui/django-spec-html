"""A spec served as a server-rendered page."""

from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView

__all__ = ["SpecConfirmView", "SpecFormSetView", "SpecFormView"]
