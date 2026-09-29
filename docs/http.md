# Serving over HTTP

`django_service_specs.http` serves an operation from a Django view with
nothing between the request and [`dispatch`](dispatching.md): no API
framework, no serializer, no router. A view reads the principal and the
arguments off the request, dispatches, and answers the result or the refusal
as JSON.

```python
from django_service_specs import AsyncSpecView, SpecView, adispatch_request, dispatch_request
```

It is the plumbing, not the pages: a view here answers JSON to a script, a
`fetch()` or an htmx request. The one page it serves is a spec's own form, from
[`SpecFormView`](#forms), for a spec a Django form validates.

## A function view

[`dispatch_request`][django_service_specs.http.dispatch_request.dispatch_request]
is the whole of what a hand-written view calls:

```python
--8<--
docs/examples/http.py:function_view
--8<--
```

It reads the principal and the arguments, dispatches with the `grant`,
`pool_seeds` and `unknown_arguments` it is given, and answers:

- **A not-found result** is 404, `{"detail": "Not found."}`.
- **A success** is the presented value, bare, with no envelope around it. The
  status is the caller's `success_status`, or else 204 for a service with
  nothing to present and 200 for everything else. A 204 has no body. A read
  whose value is `None` (an `allow_none` retrieve that found nothing) answers
  `null` at 200, since `None` is its value.
- **A refusal of either family** is
  [`error_response`](#refusals)'s answer. Anything else propagates, a
  configuration error included: it is wrong for every caller, so it belongs to
  the host's error handling rather than to one client's response.

[`adispatch_request`][django_service_specs.http.adispatch_request.adispatch_request]
does the same from an async view, through `adispatch` and `apresent`. It reads
the request in an executor hop of its own, because touching `request.user` is
a session query and Django refuses one on the event loop.

## The class form

[`SpecView`][django_service_specs.http.spec_view.SpecView] is one spec as a
view, and every attribute can be passed to `as_view()` or set on a subclass:
`spec`, `success_status`, `unknown_arguments`, `pool_seeds` and `methods`.

```python
--8<--
docs/examples/http.py:urls
--8<--
```

**The methods follow the spec.** A `SelectorSpec` answers GET and HEAD, a
`ServiceSpec` POST. `methods` replaces that with the ones it names, from GET,
HEAD, POST, PUT, PATCH and DELETE, in either case. Any other method is Django's
own 405, with an `Allow` header listing exactly the methods served, and
OPTIONS is Django's own answer.

`as_view()` checks the spec and the methods when the view is built, so a view
with no spec, or one naming a method it cannot serve, fails when the URLconf is
imported rather than on its first request.

[`AsyncSpecView`][django_service_specs.http.async_spec_view.AsyncSpecView] is
the same view with every handler async, through `adispatch_request`. Django
serves a class async only when all of its handlers are, which is why it is a
class of its own rather than a setting.

## Arguments

[`request_arguments`][django_service_specs.http.request_arguments.request_arguments]
reads them, by method and body:

| Request | Read from | Coerced |
| --- | --- | --- |
| GET, HEAD | the query string | yes, with `coerce_flat` |
| any other method, `application/json` body | the body, which must be an object | no |
| any other method, form or multipart body | the form fields | yes, with `coerce_flat` |
| any other method, no body | nothing | - |
| any other method, any other body | refused, 415 | - |

**A flat source is read by the declaration.** An `array` parameter takes every
value its key was sent with (`?ids=1&ids=2`), and anything else takes the last.
A blank value is absent unless `""` is one the parameter can take: a plain
string can, and a number, a boolean, a date, a decimal, or a string whose
`choices` leave the blank out cannot. A flat wire has no other spelling of
"left blank", and an empty `?count=` or `?since=` from a filter form is not a
refusal to count or a malformed date. Django's `csrfmiddlewaretoken` is never an argument. Then
[`coerce_flat`](arguments.md#flat-transports) types the strings, and leaves an
undeclared key for the closed argument set to refuse under the caller's policy.

**A JSON body is read as it is.** Its values are not coerced, because a JSON
caller that sends `"5"` for an integer has sent a string, and the shape check
says so. A body that does not parse, or is not an object, is refused as
`InvalidArguments` under `non_field_errors`. An empty body is no arguments.

**A body in any other format is refused** as
[`UnsupportedMediaType`][django_service_specs.http.unsupported_media_type.UnsupportedMediaType],
answered 415, rather than read as no arguments: a PATCH sent as `text/plain`
or `application/merge-patch+json` to an operation whose parameters are all
optional would otherwise run with nothing and answer success. A request with
no body carries no arguments whatever its `Content-Type` says, so a
`fetch(url, {method: "POST"})` for an operation its route names is served.

Django parses a form body into `request.POST` for POST alone, so a PUT, PATCH
or DELETE form is parsed here the same way rather than arriving empty. **Files
are not arguments**: a parameter has no file type. A POST's uploads are in
`request.FILES` for the view, since Django parses them; it fills
`request.FILES` for POST alone, and the uploads in a PUT, PATCH or DELETE body
parsed here are dropped.

**The URL kwargs are arguments too**, merged last so the route wins a clash, as
it does in djangorestframework-services. A client-supplied value must not move
the route's scope: a POST to `/notes/4/rename/` whose body says `"pk": 9`
renames note 4 or nothing. A path segment with no converter arrives as a
string and goes through `coerce_flat` like a query value. Every URL kwarg is
an argument, so the spec declares each one. A route capturing a kwarg its spec
does not declare raises `ImproperlyConfigured` on the first request it
serves, under either `unknown_arguments` policy: the mismatch is the host's,
wrong for every request, and a 400 would tell the client it had sent
something wrong. A host scoping by a kwarg its spec does not take (an `org`
read by middleware, say) calls
[`dispatch_request`][django_service_specs.http.dispatch_request.dispatch_request]
from its own view and passes the `url_kwargs` the spec declares.

## The principal

The principal is `request.user`, **anonymous included**: the spec's permission
check is what decides whether anonymous may act, so it is handed the anonymous
user rather than being pre-empted.

**A deactivated account is refused**, as `PrincipalUnavailable` (403), before
anything else is read. Django's `ModelBackend` never logs one in, but
`AllowAllUsersModelBackend` and a project's own backend may, and a deactivated
principal never acts, on HTTP or off it.

## Refusals

[`error_response`][django_service_specs.http.error_response.error_response]
answers both [families](arguments.md#two-families-of-error), with the statuses
djangorestframework-services uses. One body differs from its answer: a
service's string or list detail, which DRF answers as a bare list and this as
a field map under `non_field_errors`, so every 400 here has one shape:

| Refusal | Status | Body |
| --- | --- | --- |
| `InvalidArguments` | 400 | the error tree |
| `ServiceValidationError` | 400 | its detail, as a field map |
| `NotPermitted`, `PrincipalUnavailable` | 403 | `{"detail": message}` |
| `ServiceNotFound` | 404 | `{"detail": message}` |
| `ServiceConflict` | 409 | `{"detail": message}` |
| `ServiceError` | 422 | `{"detail": message}` |
| `UnsupportedMediaType` | 415 | `{"detail": message}` |
| `DispatchError` | 400 | `{"detail": message}` |

A row is read top to bottom and the first match answers, so a subclass is
matched before its base. **Every 400 body is a field map**: a
`ServiceValidationError`'s string or list detail goes under
`non_field_errors`, where a message about the input as a whole sits in the
tree too, and the tree's integer row keys are strings on the wire. Messages
render in the active language, lazy ones included.

**Never 401.** A session has no challenge to answer, and a 401 tells a client
to start an authentication flow it has no way to finish. An anonymous caller
a permission check refuses is a 403, like any other refused principal.

## CSRF

**CSRF is the host's middleware**, as it is for any Django view. Nothing in the
package is exempted: a POST from a session-authenticated browser carries the
token, in the `X-CSRFToken` header for a `fetch()` or in the form for an HTML
form, and `CsrfViewMiddleware` refuses one that does not. A route that needs no
token is the host's decision, and one line:

```python
--8<--
docs/examples/http.py:csrf
--8<--
```

## Forms

[`SpecFormView`][django_service_specs.http.spec_form_view.SpecFormView] serves
a spec whose Validator is a [`FormValidator`](forms.md) as the page a person
fills that form in on: the form that validates the arguments is the form the
page renders.

```python
--8<--
docs/examples/http_forms.py:spec
--8<--
```

```python
--8<--
docs/examples/http_forms.py:urls
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
`PermissionDenied`, which the host's 403 page answers. The form is always
unbound; showing an update's current row as its initial data is a page of its
own, which this view does not build.

**POST starts as GET does**: the route first, then the same check, and only
then is the form built from the post. A refused post is answered with the
page again, and dispatch's shape check comes before its own permission check,
so without it a principal the spec refuses would be shown the page - every
row a choice field lists, and `extra_context` - by posting a malformed form,
and a form whose constructor reads rows would read them for that principal.
The `Grant` the check returns goes to dispatch, so it runs once; the
object-level check is still dispatch's.

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
- The URL kwargs are merged last, so the route wins a clash, and typed with
  [`coerce_flat`](arguments.md#flat-transports) in the same call as
  everything else: one refusal carries every problem. Each is an argument,
  so the spec declares each one. A kwarg the route captures and the spec does
  not declare is the host's misconfiguration, wrong for every caller, so it
  raises `ImproperlyConfigured` under either `unknown_arguments` policy - the
  policy governs what a client sends - rather than blaming the client with a
  refused post.

Then [`dispatch`](dispatching.md). **A success redirects** to
`get_success_url(result)`, which resolves `success_url` as Django's
`redirect()` does, so a path, a `reverse_lazy` or a URL name all work. Override
it to read the result, as `AddBookThenShowIt` does to reach the book it
created. A refusal:

| Refusal | The form view answers |
| --- | --- |
| `InvalidArguments`, `ServiceValidationError` | 400, the form re-rendered |
| `ServiceConflict` | 409, the form re-rendered |
| `ServiceError` | 422, the form re-rendered |
| `DispatchError` | 400, the form re-rendered |
| `NotPermitted`, `PrincipalUnavailable` | `PermissionDenied` |
| `ServiceNotFound`, a not-found result | `Http404` |

The first two rows place the refusal on the form, and a service's string or
list detail is about the whole form. Every other re-rendered refusal is its
message, among the form's non-field errors. **The re-rendered form carries
the refusal and nothing else**, so the page says what dispatch decided. The
page's own form is bound to no row, so its own errors would tell an update
page that an unchanged unique value was taken. So after a shape-check
refusal - a required field left blank, a date that does not parse - the
form's further checks, such as a length, `clean()` or uniqueness, answer the
next post rather than this one. The statuses are
[`error_response`](#refusals)'s, so a client reading a refused post - htmx,
or Turbo, which will not render a failed post answered 200 - reads it the
same way it reads the JSON views.

`as_view()` refuses, when the URLconf is imported, a view with no
`ServiceSpec`, a spec whose Validator is not a `FormValidator`, and a view
with neither a `success_url` nor its own `get_success_url`. The view is **sync
only** in this release.

### A refusal, placed on the form

[`add_argument_errors`][django_service_specs.http.add_argument_errors.add_argument_errors]
is how the view places a refusal, on a form whose own errors it has cleared,
and a hand-written view with its own bound form can call it the same way. It
uses Django's public `form.add_error`, beside whatever errors the form carries:

- **A key naming one of the form's fields** puts its messages on that field.
- `non_field_errors`, and Django's own `"__all__"`, go to the form's non-field
  errors.
- **Any other key** - a URL kwarg such as `pk`, or a key a service chose -
  goes to the non-field errors prefixed with its path: `"pk: Enter a whole
  number."`. A nested tree is flattened with its path joined by dots,
  `"books.1.title: This field is required."`, since a form is flat. Below a
  field the path is an array's element index, which means nothing to a
  person reading one control, so it is dropped there.
- **Never a duplicate.** A message the form already carries at the same place
  is not added again. The form validating the same post says what the kernel
  says - a `FormValidator`'s refusal is the form's own errors, and the shape
  check spells its messages as Django's fields do - so without this most
  refusals would show twice.
