"""A django-service-specs spec as a server-rendered page, plus the plain-Django JSON views.

``views`` serves a spec as the page its form is, and ``http`` serves one as
JSON with nothing between the request and the kernel's ``dispatch``. ``types``
holds ``Outcome``, the vocabulary a page's post ends in. Every public name of
the three is here; read them from the root.
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
from django_spec_html.types.outcome import Outcome
from django_spec_html.version import __version__
from django_spec_html.views.spec_form_view import SpecFormView

__all__ = [
    "AsyncSpecView",
    "Outcome",
    "SpecFormView",
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
