# django-spec-html

[![CI](https://github.com/Artui/django-spec-html/workflows/tests/badge.svg)](https://github.com/Artui/django-spec-html/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Django versions](https://img.shields.io/pypi/djversions/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-spec-html/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-spec-html/gh-pages/coverage.json)](https://github.com/Artui/django-spec-html/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-spec-html.svg)](https://github.com/Artui/django-spec-html/blob/main/LICENSE)


A [`django-service-specs`](https://github.com/Artui/django-service-specs) spec
served over plain Django, with no API framework between the request and the
kernel's `dispatch`. `SpecFormView` serves a spec whose Validator is a Django
form as the page a person fills that form in on, and a refused post comes back
as the page again with the refusal placed on its form. An htmx request is
answered with a fragment of the same page at the same URL: a refusal the page
re-renders is the form at 200, since htmx swaps no 4xx by default, a denial is
raised for the host's handlers as on the page, and a success is `HX-Redirect`
or a result fragment rendered in place.
`SpecSelectorView` serves a selector spec as a page to read: a list a person
filters and pages through, optionally as a django-tables2 table, or one row's
page, with each row answering what its operations allow, and why not, through
the `available` and `unavailable` template filters, in the page's one query.
`SpecActionsMixin` serves the same pages inside the Django admin, as a
`ModelAdmin`'s actions: an object action linked from a row's change form, and a
changelist action over the rows ticked, each within the admin's own scope.
Beside the pages, `SpecView`, `AsyncSpecView` and `dispatch_request` serve a
spec as JSON, for a script or a `fetch()`.

Both halves are the kernel's, moved out with their history so the kernel
carries no transport: the JSON views as kernel 0.11.0 shipped them, and
`SpecFormView` as kernel 0.5.1 did. The behaviour is the kernel's, but for
the page answering an `UnsupportedMediaType` an operation raises at 415 where
the kernel's answered 400; the import path is this package's. The kernel no
longer ships these names: import them from `django_spec_html`.

## Install

```bash
pip install django-spec-html
```

Python 3.10 or later, Django 4.2 or later, and `django-service-specs` 0.21.1 or
later, which it installs. The JSON views need no entry in `INSTALLED_APPS`; the
pages' default templates are found as any app's are, so list `django_spec_html`
there to use them, or give each page a `template_name` of its own.

## Quickstart

A page, for a spec whose Validator is a `FormValidator`. GET renders the form;
a valid POST dispatches and redirects; a refused one renders the form again
with the refusal on it:

```python
# yourapp/urls.py
from django.urls import path, reverse_lazy
from django_spec_html import SpecFormView

from yourapp.specs import add_book_spec  # a ServiceSpec with a FormValidator

urlpatterns = [
    path(
        "books/add/",
        SpecFormView.as_view(
            spec=add_book_spec,
            template_name="books/add.html",  # renders {{ form }} in a POST form
            success_url=reverse_lazy("books"),
        ),
    ),
]
```

A JSON view, for any spec. The view reads the arguments from the query string
or the body and the route, dispatches, and answers the presented result, or
the refusal at its status:

```python
from django.urls import path
from django_spec_html import SpecView

from yourapp.specs import list_notes_spec, rename_note_spec

urlpatterns = [
    path("notes/", SpecView.as_view(spec=list_notes_spec)),
    path("notes/<int:pk>/rename/", SpecView.as_view(spec=rename_note_spec)),
]
```

## Documentation

<https://artui.github.io/django-spec-html/>

## License

MIT. See [LICENSE](https://github.com/Artui/django-spec-html/blob/main/LICENSE).
