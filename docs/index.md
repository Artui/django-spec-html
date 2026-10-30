# django-spec-html

A [`django-service-specs`](https://artui.github.io/django-service-specs/) spec served over plain Django, with no API
framework between the request and the kernel's
[`dispatch`](https://artui.github.io/django-service-specs/dispatching/). Two halves,
and the admin face the pages are served from too:

- **[Pages](pages.md).**
  [`SpecFormView`][django_spec_html.views.spec_form_view.SpecFormView] serves
  a spec whose Validator is a Django form as the page a person fills that form
  in on. A refused post comes back as the page again, with the refusal placed
  on its form. An htmx request is answered with a fragment of the same page
  ([an htmx request](pages.md#an-htmx-request)).
- **[Serving a read](selectors.md).**
  [`SpecSelectorView`][django_spec_html.views.spec_selector_view.SpecSelectorView]
  serves a selector spec as a list page, filtered and paged, or as one row's
  page, and the `available` and `unavailable` filters ask each row what its
  operations allow and why not.
- **[JSON views](http.md).**
  [`SpecView`][django_spec_html.http.spec_view.SpecView],
  [`AsyncSpecView`][django_spec_html.http.async_spec_view.AsyncSpecView] and
  the functions they are built from serve a spec as JSON, for a script or a
  `fetch()`.
- **[In the admin](admin.md).**
  [`SpecActionsMixin`][django_spec_html.model_admin.spec_actions_mixin.SpecActionsMixin]
  serves a spec's page as a Django admin action, on one row from its change
  page or on the rows selected in its changelist, inside the admin's chrome
  and its scope.

```python
from django_spec_html import SpecActionsMixin, SpecFormView, SpecSelectorView, SpecView
```

## Installing

```bash
pip install django-spec-html
```

It installs [`django-service-specs`](https://artui.github.io/django-service-specs/), the kernel whose specs it serves,
from 0.21.0, on Python 3.10 and Django 4.2 or later. The JSON views need no
entry in `INSTALLED_APPS`; the pages' default templates are found as any app's
are, so list `django_spec_html` there to use them, or give each page a
`template_name` of its own ([the page's template](pages.md#the-pages-template)).

Both halves were the kernel's, and moved here so the kernel carries no
transport: the JSON views as kernel 0.11.0 shipped them, and `SpecFormView`
as kernel 0.5.1 did. The behaviour is the kernel's, but for the page
answering an `UnsupportedMediaType` an operation raises at 415 where the
kernel's answered 400 ([refusals on the page](pages.md)); the import path is
this package's. The kernel no longer ships these names: import them from
`django_spec_html`.
