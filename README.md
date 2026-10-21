# django-spec-html

[![CI](https://github.com/Artui/django-spec-html/workflows/tests/badge.svg)](https://github.com/Artui/django-spec-html/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Django versions](https://img.shields.io/pypi/djversions/django-spec-html.svg)](https://pypi.org/project/django-spec-html/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-spec-html/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-spec-html/gh-pages/coverage.json)](https://github.com/Artui/django-spec-html/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-spec-html.svg)](LICENSE)


A [`django-service-specs`](https://github.com/Artui/django-service-specs) spec
served over plain Django, with no API framework between the request and the
kernel's `dispatch`. `SpecFormView` serves a spec whose Validator is a Django
form as the page a person fills that form in on, and a refused post comes back
as the page again with the refusal placed on its form. Beside the pages,
`SpecView`, `AsyncSpecView` and `dispatch_request` serve a spec as JSON, for a
script, a `fetch()` or an htmx request.

Both halves are the kernel's, moved out with their history so the kernel
carries no transport: the JSON views as kernel 0.11.0 shipped them, and
`SpecFormView` as kernel 0.5.1 did. The behaviour is the kernel's; the import
path is this package's. Until the kernel release that removes its own copy,
the kernel's root still exports the ten JSON names, and those are the kernel's
copy: import them from `django_spec_html`.

## Install

```bash
pip install django-spec-html
```

Python 3.10 or later, Django 4.2 or later, and `django-service-specs` 0.10.0 or
later, which it installs. Neither package needs an entry in `INSTALLED_APPS`.

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

MIT. See [LICENSE](LICENSE).
