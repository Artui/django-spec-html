"""Turning htmx on: the page's own template, and host 403 and 404 handlers that answer htmx.

``tests/docs/test_spec_form_examples.py`` serves the route below to a browser's
request and to htmx's, and calls the handler with each, so the page cannot
describe a fragment or a redirect the view and the handler do not answer.
Below Django 6.0 it serves them under ``TEMPLATES`` as this module sets it,
the setting a project on an older Django adds.
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.urls import path, reverse_lazy
from django.utils.cache import patch_vary_headers
from django.views.defaults import page_not_found, permission_denied

from django_spec_html import SpecFormView
from docs.examples.spec_form import add_book_spec, books

# The suite's own templates, where this example's page template lives.
BASE_DIR = Path(__file__).resolve().parents[2] / "tests"

# --8<-- [start:partials]
# settings.py, below Django 6.0 only: from 6.0 Django has the tag and the
# address itself, and this would register a second partialdef over its own.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "OPTIONS": {
            # The tag as a builtin, so the page's template needs no
            # {% load partials %}, which Django 6.0 would not find.
            "builtins": ["template_partials.templatetags.partials"],
            # Its loader around the project's own, for the page.html#name
            # address. Adding "template_partials" to INSTALLED_APPS sets this
            # one up for you, and the builtin above still has to be named.
            "loaders": [
                (
                    "template_partials.loader.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
        },
    }
]
# --8<-- [end:partials]

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


# --8<-- [start:denials]
def handler403(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """The host's 403: sign in for a visitor who is not, and for htmx a header saying where to go."""
    if request.headers.get("HX-Request") != "true":
        if request.user.is_authenticated:
            response = permission_denied(request, exception)
        else:
            # Nobody to refuse: the page they asked for is one sign-in away.
            response = redirect_to_login(request.get_full_path())
    else:
        # htmx swaps no 4xx body, but it acts on these two headers whatever
        # the status, so the 403 keeps its status and still reaches the visitor.
        response = HttpResponse(status=403)
        if request.user.is_authenticated:
            # Signed in and still refused: reload. A page that refuses this
            # principal on GET answers the reload with its 403 page; a denial
            # only the operation raises does not recur on GET, so the reload
            # shows the form again, empty.
            response["HX-Refresh"] = "true"
        else:
            # Sign in, then come back to the page the form is on.
            here = request.headers.get("HX-Current-URL", request.get_full_path())
            response["HX-Redirect"] = redirect_to_login(here).url
    # One URL, two answers: a cache must not hand one to the other.
    patch_vary_headers(response, ("HX-Request",))
    return response


def handler404(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """The host's 404: Django's page for a browser, and for htmx a refresh."""
    if request.headers.get("HX-Request") != "true":
        response = page_not_found(request, exception)
    else:
        # A row's action on a page that has gone stale: htmx swaps no 4xx body
        # and would show nothing, so the page is reloaded to show what is there now.
        response = HttpResponse(status=404)
        response["HX-Refresh"] = "true"
    patch_vary_headers(response, ("HX-Request",))
    return response


# --8<-- [end:denials]
