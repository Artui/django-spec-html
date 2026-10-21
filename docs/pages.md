# Serving a page

A spec served as a server-rendered page: the form that validates a post is the
form the page renders, every page works as a plain form post, and a refused
post comes back as the page again with the refusal placed on its form. The
statuses a refused post answers are the [JSON views'](http.md#refusals), so a
client reading one reads the other the same way.


[`SpecFormView`][django_spec_html.views.spec_form_view.SpecFormView] serves
a spec whose Validator is a [`FormValidator`](https://artui.github.io/django-service-specs/forms/) as the page a person
fills that form in on: the form that validates the arguments is the form the
page renders.

```python
from django_spec_html import SpecFormView
```

The examples on this page serve a book's form, a plain Django form read as
the spec's Validator:

```python
--8<--
docs/examples/spec_form.py:form
--8<--
```

```python
--8<--
docs/examples/spec_form.py:spec
--8<--
```

```python
--8<--
docs/examples/spec_form.py:urls
--8<--
```

The template is any template that renders the form inside a POST form with
its token. The one above is as small as that:

```html
--8<--
tests/templates/spec_form.html
--8<--
```

**GET** renders `template_name` with an unbound form under `form`, and `view`
and `spec` beside it: the names Django's `FormView` uses, with
`extra_context` merged in as Django's `ContextMixin` does. The spec's
class-level permission check runs first, so the page is never offered to a
principal the post would refuse: an anonymous visitor above gets Django's own
`PermissionDenied`, which the host's 403 page answers.

**An update's page shows the row it updates.** Every GET then finds its
target as dispatch would, through
[`resolve_target`][django_service_specs.dispatch.resolve_target.resolve_target]
on the route's kwargs, coerced as a post's are: the row or set a target
selector resolves, or `None` for a create.

- A check refusing it - the object-level check on a row, the scope check on a
  set or on `None` - raises `PermissionDenied`, as the post would be refused;
  a missing row, or a selector's `ServiceNotFound`, is `Http404`; and a route
  value the spec's parameters refuse is Django's `BadRequest`, which the
  host's 400 page answers.
- The form stays unbound, and shows the row's values for its own fields. A
  `ModelForm` of the row's model is built on a copy of the row, as the
  [`FormValidator`][django_service_specs.adapters.forms.form_validator.FormValidator]
  binds it, so its own `__init__` sees the row it edits. Any other form takes
  the row's values for the fields it names as its `initial`, and a field no
  model field matches keeps its own, as does a disabled one, which the post
  cleans from that initial rather than reading.
- So a file the row holds is shown, linked, with the
  [clear checkbox](https://artui.github.io/django-service-specs/forms/#clearing-a-file) an optional file's
  `ClearableFileInput` offers beside it.
- A create, a collection target and a missing row the instance selector's
  `allow_none` lets through show an empty form.

```python
--8<--
docs/examples/spec_form.py:update
--8<--
```

The URLconf above routes it as `books/<int:pk>/edit/`.

**POST starts as GET does**: the route first, then the same check, and only
then is the form built from the post. A refused post is answered with the
page again, and dispatch's shape check comes before its own permission check,
so without it a principal the spec refuses would be shown the page - every
row a choice field lists, and `extra_context` - by posting a malformed form,
and a form whose constructor reads rows would read them for that principal.
The `Grant` the check returns goes to dispatch, so it runs once.

**Then the post finds its target, as the page does**, before the form is
built or read. So the form a refused post re-renders is built on the row as
the page's was, and shows its held file and the clear checkbox beside it. And
the target answers **before the shape check**: a principal the object-level or
scope check refuses is `PermissionDenied`, and a missing row `Http404`, even
for a post that is malformed, where dispatch's own order would refuse the
malformed post first and re-render the page for them. Dispatch is handed the
target and the grant `resolve_target` returns, so it looks nothing up again and
asks no check twice. One case is left to dispatch: a route value the target's
reads refuse, which comes back in dispatch's one refusal beside the form's
own, as a non-field error. A create resolves `None` and a collection target
its set, each handed on the same way.

**POST** then binds the form to the post and reads the arguments **through
the form's own widgets**, which is how Django reads a form:

- A checkbox is `True` or `False` and a multi-select a list, where a flat
  reading would refuse a checkbox's `"on"`. Only the form's fields are read,
  so the CSRF token and a named submit button never become arguments, and a
  disabled field is not read at all, since the form ignores what is posted
  for it.
- **A field left blank is absent**, for every field, as it is to the form. An
  optional date, number or choice left empty is simply not sent, and a
  required field left empty is refused with `"This field is required."`,
  once.
- **A number, a date or a date-time in one of the field's input formats** -
  `10/25/1974`, a localized `12,50`, `5.0` for an integer - is read by the
  field's own `to_python` and sent on in the form the shape check reads: an
  ISO date, an ISO date-time with its offset, a decimal string, a number. So
  the shape check never refuses what the form accepts. A value the field
  refuses is sent on as it came, and refused in the kernel's words, which are
  the field's own.
- **A ticked [clear checkbox](https://artui.github.io/django-service-specs/forms/#clearing-a-file)** is read by its
  file's widget, which answers `False`, and is sent on as the argument the
  Validator declares for it, `attachment-clear`, so the file is cleared.
  Ticked beside an upload, both are sent, and the form refuses the pair in
  its own words.
- The URL kwargs are merged last, so the route wins a clash, and typed with
  [`coerce_flat`](https://artui.github.io/django-service-specs/arguments/#flat-transports) in the same call as
  everything else: one refusal carries every problem. Each is an argument,
  so the spec declares each one. A kwarg the route captures and the spec does
  not declare is the host's misconfiguration, wrong for every caller, so it
  raises `ImproperlyConfigured` under either `unknown_arguments` policy - the
  policy governs what a client sends - rather than blaming the client with a
  refused post.

Then [`dispatch`](https://artui.github.io/django-service-specs/dispatching/). **A success redirects** to
`get_success_url(result)`, which resolves `success_url` as Django's
`redirect()` does, so a path, a `reverse_lazy` or a URL name all work. Override
it to read the result, as `AddBookThenShowIt` does to reach the book it
created. A refusal:

| Refusal | The form view answers |
| --- | --- |
| `InvalidArguments`, `ServiceValidationError` | 400, the form re-rendered |
| `ActionUnavailable`, `ServiceConflict` | 409, the form re-rendered |
| `AdditionalInputRequired`, `ServiceError` | 422, the form re-rendered |
| `UnsupportedMediaType` | 415, the form re-rendered |
| `DispatchError` | 400, the form re-rendered |
| `NotPermitted`, `PrincipalUnavailable` | `PermissionDenied` |
| `ServiceNotFound`, a not-found result | `Http404` |

The first two rows place the refusal on the form, and a service's string or
list detail is about the whole form. Every other re-rendered refusal is its
message, among the form's non-field errors. **The re-rendered form carries
the refusal and nothing else**, so the page says what dispatch decided. The
page's own form is never validated: its errors start empty and the refusal is
placed on it, so nothing the Validator did not decide reaches it, and a value
the Validator refused because no column holds it - a key past 64 bits - never
reaches the database through the page's own checks. So after a shape-check
refusal - a required field left blank, a date that does not parse - the
form's further checks, such as a length, `clean()` or uniqueness, answer the
next post rather than this one. The statuses are
[`error_response`](http.md#refusals)'s, so a client reading a refused post - Turbo,
which will not render a failed post answered 200 - reads its outcome from the
status.

`as_view()` refuses, when the URLconf is imported, a view with no
`ServiceSpec`, a spec whose Validator is not a `FormValidator`, a `many`
spec, whose list of rows no form posts, and a view with neither a
`success_url` nor its own `get_success_url`. The view is **sync
only** in this release.

<a id="htmx"></a>
**The page reads no htmx header** in this release, so an htmx request is
answered as any other post. A success is the redirect to the success URL, which
the request follows itself, so the page that URL serves is swapped into the
request's target. A refusal is the whole page at its 4xx status, which htmx does not swap
by default. A fragment answer for an htmx request, with `HX-Redirect` on a
success, is planned for 0.2.0. Until then, serve an htmx form from a view of
your own.
