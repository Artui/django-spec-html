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
    if request.headers.get("HX-Request") != "true":
        if request.user.is_authenticated:
            response = permission_denied(request, exception)
        else:
            response = redirect_to_login(request.get_full_path())
    else:
        response = HttpResponse(status=403)
        if request.user.is_authenticated:
            response["HX-Refresh"] = "true"
        else:
            here = request.headers.get("HX-Current-URL", request.get_full_path())
            response["HX-Redirect"] = redirect_to_login(here).url
    patch_vary_headers(response, ("HX-Request",))
    return response


def handler404(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """The host's 404: Django's page for a browser, and for htmx a refresh."""
    if request.headers.get("HX-Request") != "true":
        response = page_not_found(request, exception)
    else:
        response = HttpResponse(status=404)
        response["HX-Refresh"] = "true"
    patch_vary_headers(response, ("HX-Request",))
    return response


# --8<-- [end:denials]
