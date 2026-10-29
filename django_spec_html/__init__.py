"""A django-service-specs spec as a server-rendered page, plus the plain-Django JSON views.

``views`` serves a spec as the page its form is, or a read as its rows, ``model_admin`` serves those
pages as a ``ModelAdmin``'s actions, and ``http`` serves a spec as JSON with
nothing between the request and the kernel's ``dispatch``. ``types`` holds
``Outcome``, the vocabulary a page's post ends in, and ``SpecAction``, the
declaration of an admin action. Every public name of the four is here; read
them from the root.
"""

from django_spec_html.http.add_argument_errors import add_argument_errors
from django_spec_html.http.adispatch_request import adispatch_request
from django_spec_html.http.async_spec_view import AsyncSpecView
from django_spec_html.http.dispatch_request import dispatch_request
from django_spec_html.http.error_response import error_response
from django_spec_html.http.request_arguments import request_arguments
from django_spec_html.http.request_principal import request_principal
from django_spec_html.http.route_arguments import route_arguments
from django_spec_html.http.spec_view import SpecView
from django_spec_html.http.unsupported_media_type import UnsupportedMediaType
from django_spec_html.model_admin.spec_actions_mixin import SpecActionsMixin
from django_spec_html.types.outcome import Outcome
from django_spec_html.types.spec_action import SpecAction
from django_spec_html.version import __version__
from django_spec_html.views.spec_confirm_view import SpecConfirmView
from django_spec_html.views.spec_form_set_view import SpecFormSetView
from django_spec_html.views.spec_form_view import SpecFormView
from django_spec_html.views.spec_selector_view import SpecSelectorView

__all__ = [
    "AsyncSpecView",
    "Outcome",
    "SpecAction",
    "SpecActionsMixin",
    "SpecConfirmView",
    "SpecFormSetView",
    "SpecFormView",
    "SpecSelectorView",
    "SpecView",
    "UnsupportedMediaType",
    "__version__",
    "add_argument_errors",
    "adispatch_request",
    "dispatch_request",
    "error_response",
    "request_arguments",
    "request_principal",
    "route_arguments",
]
