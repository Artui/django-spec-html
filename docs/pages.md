# Serving a page

A spec served as a server-rendered page: the form that validates a post is the
form the page renders, every page works as a plain form post, and a refused
post comes back as the page again with the refusal placed on its form. The
statuses a refused post answers are the [JSON views'](http.md#refusals), so a
client reading one reads the other the same way. An htmx request to the same
URL gets the part of the page it swaps, [from the same template](#htmx).


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
created. A refusal ends in one of four outcomes, read from the same map as
its status, and each is answered as the page or, for htmx, as
[the fragment](#htmx):

| Refusal | Outcome | The page answers | The fragment answers |
| --- | --- | --- | --- |
| `InvalidArguments`, `ServiceValidationError` | invalid | 400, the form re-rendered | 200, the form fragment re-rendered |
| `UnsupportedMediaType` | invalid | 415, the form re-rendered | 200, the form fragment re-rendered |
| `AdditionalInputRequired` | needs_input | 422, the form re-rendered | 200, the form fragment re-rendered |
| `ActionUnavailable`, `ServiceConflict` | failed | 409, the form re-rendered | 200, the form fragment re-rendered |
| `ServiceError` | failed | 422, the form re-rendered | 200, the form fragment re-rendered |
| `DispatchError` | failed | 400, the form re-rendered | 200, the form fragment re-rendered |
| `NotPermitted`, `PrincipalUnavailable` | denied | `PermissionDenied` | `PermissionDenied` |
| `ServiceNotFound` | denied | `Http404` | `Http404` |

A not-found result is `Http404` too, in both. The outcome column is the
package's `Outcome`, by value; a success is `ok`.

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
`success_url` nor its own `get_success_url`; then every check the JSON views
make of the request's hooks, [below](#what-the-request-carries). The view is
**sync only** in this release.

## What the request carries

The page reads the request as [the JSON views](http.md#what-the-request-carries)
do, with the same attributes and hooks, checked by the same code:

- **`route_kwargs`** says which URL kwargs fill which parameters, a mapping
  to rename or drop (`{"branch": None}`) or a tuple of names to allow, as on
  [`SpecView`](http.md#kwargs-a-spec-does-not-read). `as_view()` refuses a
  bare string and a target or an allowed name the spec does not declare, and
  two kwargs filling one parameter are refused on the request that captures
  both.
- **`get_pool_seeds(request)`**, `pool_seeds` unless overridden, answers the
  seeds this request's call is resolved with. It is asked once per request,
  and every step that resolves a spec callable is handed what it answered:
  the target's lookup and its object-level or scope check, the affordances
  [below](#an-operation-that-is-unavailable), and dispatch. Seed what the
  request carries, never the request itself. **The class-level check is the
  exception**: the page asks `has_permission` before it reads anything else,
  and asks it with no seed at all, its own `pool_seeds` included, so a check
  naming a seed raises `TypeError` on the page. The JSON views hand that check
  its seeds.
- **`get_server_arguments(request)`**, `server_arguments` unless overridden,
  answers the kernel's
  [`ServerArguments`][django_service_specs.types.server_arguments.ServerArguments],
  and three readers of the call read the server's value for each name it
  answers for: the page's form, the target's lookup and dispatch. **A field of
  the page's form that the binding binds renders disabled, showing the
  server's value; one it withholds renders disabled and empty**, on GET and
  on a refused post alike, so the page never shows a value the client posted
  for it. A disabled field is not read from the post, so a value the client
  was never offered is never coerced or refused; the binding is laid over the
  post's arguments before dispatch, and over the route's before the target is
  found, so a target selector reading a bound parameter finds the row by the
  server's value. Anything else a template shows - the row's own fields, say -
  is what it is.

The checks are the JSON views': `as_view()` refuses an `async def` hook,
checks `server_arguments` with the kernel's `check_server_arguments` against
the declared `pool_seeds`, and refuses a `route_kwargs` filling a name the
binding answers for. On each request, a per-call binding that binds or
withholds other names than the declared one, a seed added per call under a
bound or withheld name, and a route capture filling a name the server answers
for are each refused as `ImproperlyConfigured`, the last pointing at
`route_kwargs={name: None}`.

```python
--8<--
docs/examples/spec_form.py:request
--8<--
```

The URLconf routes it as `branches/<branch>/books/new/`. Its status renders
disabled with `draft` selected, and a posted status is dropped: every book it
adds is a draft.

## An operation that is unavailable

A spec's [`Affordance`][django_service_specs.types.affordance.Affordance]
conditions say whether the operation is possible right now, and **the page
asks them on GET**, after it has found the target, so it never offers a form
whose post the spec would refuse at 409:

- **The callable conditions are asked always**, through the kernel's
  `unmet_operation_affordance`, on a pool `base_pool` builds from the call's
  own seeds. So a condition reading a seed `get_pool_seeds` adds, as
  `branch_open` above reads `branch`, is asked with it.
- **When the target is a row and every callable condition is met, the
  conditions on the row are asked too**, as dispatch asks them. A target of
  `None` - a create, or a missing row `allow_none` lets through - is no row,
  and no row condition is asked about it.
- **The first unmet one is in the context as `unavailable`**: the
  `Affordance` itself, whose `code` and `reason` a template reads. **When
  every condition is met, `unavailable` is absent** from the context, not
  `None`. The fragment renders from the same context, so an `hx-get` that
  loads the form carries it too.

**Every callable condition is asked twice on a GET with a row**: once alone,
then again with the row's conditions, since the kernel's `enforce_affordances`
asks them all as dispatch does. Each seed is resolved twice as well, once for
the target's lookup and once for the affordances' pool. Write conditions and
seed resolvers that may be asked more than once and answer the same.

The callable conditions are asked first, without a row, so where both kinds
are unmet and the row's is declared first, the page names the callable while
the post is refused with the row's: dispatch asks in declaration order. **The
page's answer is advice, and the post's refusal is the authority**: the POST
is unchanged, dispatch asks every condition again, and a condition that
changed between the two requests is answered by the 409.

**A refused post carries the refusal's `code`** in its context, beside the
form, for a refusal that has one: `ActionUnavailable`, whose `code` is the
one the [JSON body](http.md#refusals) carries beside its `detail`. It is
absent for every other refusal. The fragment's context carries it too, so a
template branches on the stable code rather than on the message.

There is no default template yet, so a host's template says it:

```html
--8<--
tests/templates/spec_form_available.html
--8<--
```

On the closed branch above, the page says why and disables the form, and a
post made anyway comes back at 409 with the reason among the form's errors and
the link to another branch beside it.

<a id="htmx"></a>
## An htmx request

**Turning htmx on is a template change.** The view answers every request with
the page unless htmx asked for less, and then answers the same outcome with the
part of the page the request swaps, rendered from the same template. Wrap the
page's form in `{% partialdef spec-form inline %}`, give the form `hx-post` and
a target, and, for a success answered in place, define `spec-result`:

```html
--8<--
tests/templates/spec_form_htmx.html
--8<--
```

```python
--8<--
docs/examples/htmx.py:urls
--8<--
```

The view's Python does not change: `inline` renders the form where it stands
on the page, and the same block is addressable on its own as
`spec_form_htmx.html#spec-form`, which is where the view looks for it by
default. **The tag and that `#name` address are Django 6.0's.** Below 6.0,
install
[django-template-partials](https://github.com/carltongibson/django-template-partials)
(23.3 or later, the first with `partialdef`) and configure it in `TEMPLATES`:
its tag as a **builtin**, and its loader around the project's own.

```python
--8<--
docs/examples/htmx.py:partials
--8<--
```

The builtin is the part that is easy to miss. Adding `"template_partials"` to
`INSTALLED_APPS` sets up the loader and nothing else, so the page's template
fails to parse at `{% partialdef %}`; and a `{% load partials %}` in the
template instead would break it again on Django 6.0, which has the tag itself
and no such library. From 6.0, configure none of this. Without either, the
address is a `TemplateDoesNotExist`. A fragment kept in a file of its own, or
under another name, is one attribute, `fragment_template_name`, and renders on
any Django.

**Which requests get the fragment.** A request whose `HX-Request` header is
`true`, unless it is boosted (`HX-Boosted`) or a history restore
(`HX-History-Restore-Request`): a boosted link or form swaps the whole body,
and a history restore after a cache miss needs the whole page back, so both get
the page. The view reads django-htmx's `request.htmx` where its middleware set
it and the three headers where it did not, so a project with django-htmx and
one without get the same answer; it imports nothing from django-htmx. The rule
is one method, `wants_fragment(request)`, to override. **Every answer sets
`Vary`** on those three headers, the page's included, since one URL answers two
bodies and a cache keyed on fewer would hand a fragment to a page load.

**What the fragment answers**, outcome by outcome:

- **GET** renders the form fragment, unbound, so an `hx-get` can load the form
  into a dialog.
- **A refusal the page re-renders** is the form fragment, bound the same way
  and from the same context, at **200** where the page answers 4xx: htmx swaps
  no 4xx response by default, so a refusal answered at one would land nowhere.
  The table above has each row, and the outcome stays in the body and in the
  map.
- **A success** is `fragment_success(result)`. Where the fragment template
  defines `spec-result`, that is rendered in place at 200, and
  `HX-Trigger: spec-succeeded` names an event for the rest of the page to
  listen for. Its context is `result`, `view`, `spec` and `extra_context`, and
  no `form`, since the post's form is done with. Otherwise it is `HX-Redirect`
  to `get_success_url(result)`, at 200 and never a 302: an XHR follows a 302
  itself and would swap the next page into the form. The URL is encoded and
  checked as the page's redirect is, so a path outside ASCII arrives as the
  page's `Location` would, and an unsafe scheme such as `javascript:` raises
  `DisallowedRedirect` in both modes. The answer passes back
  through the host's middleware like any other, so a cookie set for the
  request, such as the session a sign-in rotates, is set on it. Override the
  method for `HX-Location`, or a 204 carrying `HX-Trigger`.
- **A denial** is raised in both modes alike, for the host's handlers.

**A template with no fragment at the address** answers htmx with Django's
`TemplateDoesNotExist` naming it - `spec_form.html#spec-form`, or the
`fragment_template_name` that is missing - and never with the page instead,
which swapped into a form's target would nest a second layout inside it.

**No htmx header reaches the spec.** The mode is read only after the outcome
is decided, to choose the wrapper: the arguments are the form's fields and the
route's kwargs, and dispatch is called the same way in both modes. So a spec
cannot branch on the mode, and its outcome on the page and in a fragment is one
outcome.

**A denial is the host's to answer, for htmx too.** The view raises
`PermissionDenied` and `Http404` in both modes, and a handler answering an htmx
request with a bare 403 shows nothing, since htmx swaps no 4xx. htmx acts on
`HX-Redirect` and `HX-Refresh` whatever the status, so a handler can keep the
403 and still send the visitor somewhere:

```python
--8<--
docs/examples/htmx.py:handler403
--8<--
```

Defined in the root URLconf as `handler403`, it sends an anonymous visitor to
sign in and back to the page the form is on, and reloads the page for a
signed-in one. **The reload answers with the 403 page only where the page
itself refuses on GET**: the spec's class-level check, or the object-level
check on the row the route names. A denial only the operation raises, from
inside the service, does not recur on GET, so the reload shows the form again,
empty, and what was typed is gone; a host whose services refuse that way
answers those in the form instead, by raising a `ServiceError`. The view's
`Vary` never reaches a denial, which is raised rather than answered, so the
handler sets its own on `HX-Request`, since it answers the two kinds of request
differently.
