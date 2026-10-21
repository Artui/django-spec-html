# Serving over HTTP

`django_spec_html.http` serves an operation from a Django view with
nothing between the request and [`dispatch`](https://artui.github.io/django-service-specs/dispatching/): no API
framework, no serializer, no router. A view reads the principal and the
arguments off the request, dispatches, and answers the result or the refusal
as JSON.

```python
from django_spec_html import AsyncSpecView, SpecView, adispatch_request, dispatch_request
```

It is the plumbing, not the pages: a view here answers JSON to a script or a
`fetch()`. A page a person reads, such as a spec's own form
rendered and re-rendered with its refusal, is a [page view](pages.md). The pieces
the entry points are built from are public so that such a view reads a request
by the same rules: [`request_principal`](#the-principal),
[`route_arguments`](#the-route-on-its-own) and
[`add_argument_errors`](#a-refusal-placed-on-a-form).

The examples on this page serve two specs, a list of the principal's notes
and a rename of one, declared as any spec is:

```python
--8<--
docs/examples/http.py:specs
--8<--
```

## A function view

[`dispatch_request`][django_spec_html.http.dispatch_request.dispatch_request]
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
  nothing to present and 200 for everything else. A 204 has no body. A service
  with nothing to present at any other status the caller names answers an
  empty body with no `Content-Type`, at that status. djangorestframework-services
  answers the same, except that where an output selector ran and found nothing
  it answers 204 whatever the caller named. Here the status stays the caller's:
  the caller named it, and an empty re-read already has a meaning, the value
  `None`. A read whose value is `None` (an `allow_none`
  retrieve that found nothing) answers `null`, since `None` is its value.
- **A refusal of either family** is
  [`error_response`](#refusals)'s answer. Anything else propagates, a
  configuration error included: it is wrong for every caller, so it belongs to
  the host's error handling rather than to one client's response.

[`adispatch_request`][django_spec_html.http.adispatch_request.adispatch_request]
does the same from an async view, through `adispatch` and `apresent`. It reads
the request in an executor hop of its own, because touching `request.user` is
a session query and Django refuses one on the event loop.

## The class form

[`SpecView`][django_spec_html.http.spec_view.SpecView] is one spec as a
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
imported rather than on its first request. The `spec` may be a declaration of
another library's, a
[`SpecSource`][django_service_specs.specs.spec_source.SpecSource]: it is
compiled there too, a declaration that does not compile is refused there, and
its methods follow the spec it compiles to.

[`AsyncSpecView`][django_spec_html.http.async_spec_view.AsyncSpecView] is
the same view with every handler async, through `adispatch_request`. Django
serves a class async only when all of its handlers are, which is why it is a
class of its own rather than a setting.

## Arguments

[`request_arguments`][django_spec_html.http.request_arguments.request_arguments]
reads them, by method and body:

| Request | Read from | Coerced |
| --- | --- | --- |
| GET, HEAD | the query string | yes, with `coerce_flat` |
| any other method, `application/json` body | the body, which must be an object | no |
| any other method, form or multipart body | the form fields, and a multipart body's uploads | yes, with `coerce_flat` |
| any other method, no body | nothing | - |
| any other method, any other body | refused, 415 | - |

**A flat source is read by the declaration.** An `array` parameter takes every
value its key was sent with (`?ids=1&ids=2`), and anything else takes the last.
An array of numbers, integers, booleans or decimal strings also reads a value
holding commas as its elements, `?ids=1,2` as `[1, 2]` with the space around
each part trimmed: a comma cannot be part of such a value, and it is the
spelling a django-filter CSV filter advertises. A decimal element stays the
string it spells, so `?pks=1,2` is `["1", "2"]`, every digit kept. Repeated keys and commas may mix, and an empty part
(`?ids=1,,2`) is refused at its index as any malformed element is. A list
longer than the array takes - its `max_length`, or 1000 by the
`SERVICE_SPECS_MAX_ARRAY_LENGTH` setting, which is the kernel's and keeps its
name - is refused whole at its name, a 400
rather than the 500 a database answers past its variable limit, and is never
split further than one element past it: see
[how long a list may be](https://artui.github.io/django-service-specs/arguments/#how-long-a-list-may-be). An array
of strings is split the same way when it declares choices and none of them
holds a comma, as a django-filter `OrderingFilter`'s do: every element must be
a choice, so `?o=title,-pk` can only be `["title", "-pk"]`. An array of
strings with no choices, or with a choice holding a comma, is never split,
since a comma can be part of a string, and an array declaring no `items` is
not either.
A blank value is absent unless `""` is one the parameter can take: a plain
string can, and a number, a boolean, a date, a decimal, or a string whose
`choices` leave the blank out cannot. A flat wire has no other spelling of
"left blank", and an empty `?count=` or `?since=` from a filter form is not a
refusal to count or a malformed date. Django's `csrfmiddlewaretoken` is never an argument. Then
[`coerce_flat`](https://artui.github.io/django-service-specs/arguments/#flat-transports) types the strings, and leaves an
undeclared key for the closed argument set to refuse under the caller's policy.

**A JSON body is read as it is.** Its values are not coerced here: a `"5"`
sent for an integer is the shape check's to read, and it reads it as 5, as it
reads one from any caller (see [the shape check](https://artui.github.io/django-service-specs/arguments/#the-shape-check)).
A body that does not parse, or is not an object, is refused as
`InvalidArguments` under `non_field_errors`. An empty body is no arguments.

**A body in any other format is refused** as
[`UnsupportedMediaType`][django_spec_html.http.unsupported_media_type.UnsupportedMediaType],
answered 415, rather than read as no arguments: a PATCH sent as `text/plain`
or `application/merge-patch+json` to an operation whose parameters are all
optional would otherwise run with nothing and answer success. A request with
no body carries no arguments whatever its `Content-Type` says, so a
`fetch(url, {method: "POST"})` for an operation its route names is served.

Django parses a form body into `request.POST` for POST alone, so a PUT, PATCH
or DELETE form is parsed here the same way rather than arriving empty. **An
upload is an argument**, under the name its part carries, as each field is:
the upload itself, Django's `UploadedFile`, for a parameter of type `file`,
every upload under the name for an array, and the last of repeated ones for
anything else. It replaces a field of the same name, since a browser sends a
file input only as a file. A POST's uploads are Django's own `request.FILES`;
those in a PUT, PATCH or DELETE body are parsed here with its fields, by
Django's own parser from the request's stream, so a file part is streamed to
the upload handlers and `DATA_UPLOAD_MAX_MEMORY_SIZE` bounds the fields alone,
as it does for a POST. Like a POST, such a body is gone once parsed: reading
`request.body` afterwards, or calling `request_arguments` on the request a
second time, raises Django's `RawPostDataException`. The entry points here
read the arguments once, so this reaches only code that reads the body after
the view - a middleware, an audit log, an error reporter. Reading
`request.body` before the view keeps the body, and the arguments are then
parsed from it as often as they are asked for; that read is a whole-body
read, so `DATA_UPLOAD_MAX_MEMORY_SIZE` bounds the whole body again, uploads
included, and a larger one raises `RequestDataTooBig`. An
upload under a name the spec does not declare reaches the closed argument set
like any undeclared field, and is refused under `REJECT`; a file input left
empty is no upload, so it is absent. Only a multipart body carries one.
An optional file's [clear checkbox](https://artui.github.io/django-service-specs/forms/#clearing-a-file) is a field of
the same body, `attachment-clear=true`, and is read as every flat `boolean`
is: `true`, `false`, `1` or `0`. A checkbox with no `value` attribute posts
`on`, which is refused, so a page posting to a `SpecView` gives it
`value="true"`.

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
[`dispatch_request`][django_spec_html.http.dispatch_request.dispatch_request]
from its own view and passes the `url_kwargs` the spec declares.

### The route on its own

[`route_arguments`][django_spec_html.http.route_arguments.route_arguments]
is that rule on its own, for a view that reads the rest of its arguments some
other way, such as through a bound form's widgets. It returns the kwargs as a
plain, uncoerced copy, or raises `ImproperlyConfigured` naming every one the
spec does not declare, so the caller merges it last and types it in the same
call as everything else, and one refusal carries every problem:

```python
--8<--
docs/examples/http.py:route_arguments
--8<--
```

## The principal

The principal is `request.user`, **anonymous included**: the spec's permission
check is what decides whether anonymous may act, so it is handed the anonymous
user rather than being pre-empted.

**A deactivated account is refused**, as `PrincipalUnavailable` (403), before
anything else is read. Django's `ModelBackend` never logs one in, but
`AllowAllUsersModelBackend` and a project's own backend may, and a deactivated
principal never acts, on HTTP or off it. `dispatch` refuses one by the same
rule, so a hand-written view handing `request.user` to it is covered too; the
entry points refuse first so that the request is never read.

[`request_principal`][django_spec_html.http.request_principal.request_principal]
is that reading, public, so a host's own view refuses by the same rule and
before it reads anything else. It raises `PrincipalUnavailable` for a
deactivated account and returns `request.user` otherwise. Reading
`request.user` is a session query, so an async view calls it off the event
loop, as `adispatch_request` does.

## Refusals

[`error_response`][django_spec_html.http.error_response.error_response]
answers both [families](https://artui.github.io/django-service-specs/arguments/#two-families-of-error), with the statuses
djangorestframework-services uses. Two bodies differ from its answer. A
service's string or list detail, which DRF answers as a bare list and this as
a field map under `non_field_errors`, so every 400 here has one shape. And an
`AdditionalInputRequired` schema, which DRF reads as an error detail and so
writes with every value a string (`"minimum": "1"`), and this writes as the
JSON Schema it is:

| Refusal | Status | Body |
| --- | --- | --- |
| `InvalidArguments` | 400 | the error tree |
| `ServiceValidationError` | 400 | its detail, as a field map |
| `NotPermitted`, `PrincipalUnavailable` | 403 | `{"detail": message}` |
| `ServiceNotFound` | 404 | `{"detail": message}` |
| `ActionUnavailable` | 409 | `{"detail": message, "code": code}` |
| `ServiceConflict` | 409 | `{"detail": message}` |
| `AdditionalInputRequired`, with a schema | 422 | `{"detail": message, "schema": schema}` |
| `ServiceError` | 422 | `{"detail": message}` |
| `UnsupportedMediaType` | 415 | `{"detail": message}` |
| `DispatchError` | 400 | `{"detail": message}` |

A row is read top to bottom and the first match answers, so a subclass is
matched before its base. **Every 400 body is a field map**: a
`ServiceValidationError`'s string or list detail goes under
`non_field_errors`, where a message about the input as a whole sits in the
tree too, and the tree's integer row keys are strings on the wire. Messages
render in the active language, lazy ones included.

**A schema no route could hear answered is not a 422.** `dispatch_request` and
`adispatch_request` pass an `AdditionalInputRequired` schema through
[`check_requested_input`][django_service_specs.dispatch.check_requested_input.check_requested_input]
before writing it: a schema naming a key the spec does not declare, or a shape
an MCP elicitation form cannot render, raises `ImproperlyConfigured` naming the
key, as it does on every other route. `error_response` alone has no spec in
hand and writes whatever it is given.

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

## A refusal, placed on a form

[`add_argument_errors`][django_spec_html.http.add_argument_errors.add_argument_errors]
places a refusal on a host's own bound form, for a page that re-renders the
form it posted with what dispatch refused. It uses Django's public
`form.add_error`, beside whatever errors the form carries:

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
- **A form not yet validated is validated first**, as reading `form.errors`
  would, so its own errors are the ones compared against, but with the guards
  a `FormValidator`'s form is validated with. Its own validation would hand
  the database driver a number no integer column holds - a model choice
  field's key, a `ModelForm`'s integer - which SQLite's refuses with
  `OverflowError`. Such a key is refused as `invalid_choice` and such an
  integer as `max_value` or `min_value`, in the words a `FormValidator`
  refuses them with, so the refusal of one is placed once. A form already
  validated is compared as it stands.
