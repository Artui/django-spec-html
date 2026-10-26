"""Turning htmx on: the page's own template, and a host 403 handler that answers htmx.

``tests/docs/test_spec_form_examples.py`` serves the route below to a browser's
request and to htmx's, and calls the handler with each, so the page cannot
describe a fragment or a redirect the view and the handler do not answer.
"""

from __future__ import annotations

from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse_lazy
from django.views.defaults import permission_denied

from django_spec_html import SpecFormView
from docs.examples.spec_form import add_book_spec, books

# --8<-- [start:urls]
urlpatterns = [
    # The same view as a plain page: only the template changed.
    path(
        "books/new/",
        SpecFormView.as_view(
            spec=add_book_spec,
            template_name="spec_form_htmx.html",
            success_url=reverse_lazy("books"),
        ),
        name="add-book",
    ),
    path("books/", books, name="books"),
]
# --8<-- [end:urls]


# --8<-- [start:handler403]
def handler403(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """The host's 403: a page for a browser, and for htmx a header saying where to go."""
    if request.headers.get("HX-Request") != "true":
        return permission_denied(request, exception)
    # htmx swaps no 4xx body, but it acts on these two headers whatever the
    # status, so the 403 keeps its status and still reaches the visitor.
    response = HttpResponse(status=403)
    if request.user.is_authenticated:
        # Signed in and still refused: reload, and the page answers the 403.
        response["HX-Refresh"] = "true"
    else:
        # Sign in, then come back to the page the form is on.
        here = request.headers.get("HX-Current-URL", request.get_full_path())
        response["HX-Redirect"] = redirect_to_login(here).url
    return response


# --8<-- [end:handler403]
