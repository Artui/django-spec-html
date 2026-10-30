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

### The page's template

**A template is optional.** The first route above names none, so it renders
`django_spec_html/spec_form.html`, the page this package ships, which is the
default `template_name`. The shipped templates are found as any app's are, so
a project rendering them lists the package as an app, with `APP_DIRS` on:

```python
INSTALLED_APPS = [
    ...,
    "django_spec_html",
]
```

`as_view()` refuses a page rendering a shipped template that the view's
`template_engine` does not find, with `ImproperlyConfigured` naming the
template and the setting, so the URLconf's import says so rather than the
first request's `TemplateDoesNotExist`. It asks for the page and its fragment,
or the page alone where `fragment_template_name` names a fragment of the
project's own or `get_fragment_template_names` is overridden, and follows neither one's `extends` or `include`: a shipped page
copied into a directory of the project's own, without the app, needs
`base.html` and `target.html` found there too. A page naming a template of its
own needs no app.

The shipped page is a plain document: no stylesheet, no script, and no htmx
attribute. Everything it shows is escaped. It renders:

- the [target pane](#what-the-page-acts-on): a set's count and when it was
  counted, and the target's display where the page sets `display_target`.
  Never the target itself, whose `str()` may be anything its model returns;
- an [unmet affordance's](#an-operation-that-is-unavailable) `reason`, with the
  form disabled, and its `code` as the paragraph's `data-code`;
- on a [partial page](#a-partial-page), a line saying a field left blank is left
  as it is. The form renders no HTML `required` there, as every partial page's
  form does;
- the form, through `form.as_div`: its non-field and field errors, and on a
  [confirmation page](#the-confirmation-page) the question's message and the
  field it asks for, each once. A refusal's `code` is the form's `data-code`,
  and a form holding a file field posts as `multipart/form-data`;
- the submit button, posting `commit_button`'s name where one is set, so a
  [post that does not commit](#a-post-that-does-not-commit) is one made without
  it.

Its form is a file of its own, `django_spec_html/spec_form_fragment.html`, which
the page includes and an [htmx request](#htmx) is answered with, on every Django:
a `{% partialdef %}` would not parse below Django 6.0 without
django-template-partials configured. It defines no `spec-result`, so a success
under htmx is `HX-Redirect`, as the page's is a redirect.

**Overriding it is the Django way.** A file of the same name in a template
directory the engine searches first replaces the shipped one:
`django_spec_html/base.html`, the document every shipped page extends, to put
all three inside a project's layout, filling its `title` and `content` blocks;
the page; or its fragment, to change the form. Or set `template_name`, and the
page is a template of the project's own, any template that renders the form
inside a POST form with its token. Every other route above does, and its
template is as small as that:

```html
--8<--
tests/templates/spec_form.html
--8<--
```

A page of the project's own carries its form for htmx as its own `spec-form`
fragment, [below](#htmx); a project overriding a shipped page keeps the shipped
fragment.

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
message, among the form's non-field errors.
An `AdditionalInputRequired` reveals the field that answers it as well, as
[the confirmation page](#the-confirmation-page).

**A refusal of the arguments shows every error in one post.** Beside an
`InvalidArguments`, the page's own form - built as the Validator's is, with
[the same keywords](#a-form-that-takes-keywords) - is validated too, and the
refusal is placed beside what it found, never twice. So after a shape-check
refusal - a required field left blank, a date that does not parse - the
form's further checks, such as a length, `clean()` or uniqueness, answer in
the same post rather than the next one: a book posted as published with no
title and no date is told both. The page's form is validated as the Validator
validates its own, under the kernel's `clean_held_to_their_columns`, so a
value no column holds - a key past 64 bits - is refused in the field's own
words and never reaches the database driver, whose SQLite raises
`OverflowError` for it. A post whose route value the target's reads refuse
never found its row, so its form is not judged as one, and carries the
refusal alone. Any other refusal - a conflict, a service's own rule,
an unavailable action - is no verdict on the arguments, and the re-rendered
form carries it alone. The statuses are
[`error_response`](http.md#refusals)'s, so a client reading a refused post - Turbo,
which will not render a failed post answered 200 - reads its outcome from the
status.

## A form that takes keywords

A form whose `__init__` takes a keyword of its own - a queryset scoped by the
principal or by a tenant - is handed it by the `FormValidator`'s
[`form_kwargs`](https://artui.github.io/django-service-specs/forms/), and the
page builds its own form with the same answer, through the kernel's
`form_keywords`: the same principal, the request's [seeds](#what-the-request-carries),
the row the post acts on (the row itself, never the copy a `ModelForm` edits)
and one call context per request, the very dict dispatch is then handed. On
GET, on a refused post and for the post's own validation, the page's form is
the Validator's, so the page offers the choices the Validator accepts:

```python
--8<--
docs/examples/spec_form.py:keywords
--8<--
```

The URLconf routes it as `books/shelve/`. A signed-in user who is not staff is
offered only the authors already on the shelves, and a post naming another is
refused on the author field; staff are offered every author.

## A partial page

A `partial` spec gets the same page, built on the same form. **The page's
form renders no HTML `required` attribute**, so the browser sends a post that
leaves a field blank, and **a blank field is absent**, as it is on every page:
on a partial call, a field the operation leaves as it is. A post with every
field blank dispatches with no arguments but what a checkbox or a
multi-select always sends, and the page does not refuse it.

```python
--8<--
docs/examples/spec_form.py:partial
--8<--
```

The URLconf routes it as `books/<int:pk>/change/`: a post with the title left
blank and a new price changes the price and keeps the title.

A blank field cannot clear a value there, since blank means "leave it": a
partial operation that clears a value needs a field of its own that says so,
as an optional file's clear checkbox does. A checkbox and a multi-select are
always sent, ticked or not, so each is written as posted. And beside a
shape-check refusal, a partial page's form carries the refusal alone: its
Validator takes the fields a post leaves out off its own form, and the page's
form, validated whole, would call each blank field required.

`as_view()` refuses, when the URLconf is imported, a view with no
`ServiceSpec`, a spec whose Validator is not a `FormValidator`, a `many`
spec, whose list of rows no form posts, and a view with neither a
`success_url` nor its own `get_success_url`; then every check the JSON views
make of the request's hooks, [below](#what-the-request-carries). The view is
**sync only** in this release.

## What the page acts on

**The page shows its target**, the row or set the operation acts on, in the
context beside the form, on GET and on every refused post it re-renders, the
fragment included:

| Key | Holds | Present |
| --- | --- | --- |
| `target` | the row an `instance_selector_spec` found, or the set a `collection_selector_spec` resolved | wherever there is a target: absent for a create, and for a missing row `allow_none` lets through |
| `target_count` | how many rows the set holds | for a set |
| `target_counted_at` | when it counted them, `django.utils.timezone.now()` | for a set |
| `target_display` | the target as a person reads it, below | on a page that sets `display_target` |

A key with nothing to hold is absent rather than `None`, as `unavailable` is.

```python
--8<--
docs/examples/spec_form.py:target
--8<--
```

The URLconf routes the first as `books/<int:pk>/withdraw/` and the second as
`books/publish-drafts/`, with `target_display_limit=3`, both with
`display_target=True` and both rendering this template:

```html
--8<--
tests/templates/spec_target.html
--8<--
```

### A bulk page

A spec with a `collection_selector_spec` acts on a set, and its page says how
many rows the set holds. **An empty set is no refusal**: its scope check is
still asked, there is no not-found exit, and the page above says "0 books
selected" where an instance page answers 404. A queryset is counted with a
`COUNT` on a fresh clone of it, so no row is read to count it; a set a
selector answers as a list is counted with `len()`.

**The count is a time-of-check, not a promise.** The rows may change between
the page and its post, so the context carries when the set was counted, and a
template says "5 books selected, counted at 14:02" rather than "5 books will
be published". The post does not replay what the page counted: it re-runs the
service on the set it finds then, and a refused post counts the set again. A
successful post counts nothing. So nothing about the count belongs in a hidden
input.

### The target's display

**A page that sets `display_target = True` shows its target as a person
reads it**, under `target_display`. It is off by default, because the
[`FieldMarking`][django_service_specs.output.field_marking.FieldMarking]s it
reads are on the spec's Output - its own presenter's, or its
`output_selector_spec`'s - and an Output describes what the operation
returns, which need not be its target: a withdrawal may return a receipt. A
page whose Output is marked serves as it did until it opts in, and asks no
presenter. On:

- the target is presented through that presenter, handed the context
  dispatch hands it: the principal, the target, the request's one call
  context, and the call's seeds as `ambient`;
- then projected by the kernel's `project_payload`: a `HIDDEN` field is
  dropped, and a choice is spoken as its display;
- and every `HANDLE` is dropped as well, at any depth. An agent keeps a handle
  to pass to another tool, and a person is never shown one.

A row's display is a dict: its `fields`, the same fields as `pairs` of name
and value in order, and `label`, the value of the field marked `LABEL`, where
one is. Loop over `pairs` rather than `fields.items`: a template looks the key
`items` up before the method, so an Output with a field of that name would
loop over its value. Above, the book is named "The Lathe of Heaven"
and its fields are its title, its price and its status, read as "Published":
the key is a handle and the publication date is hidden. **A set's display is
a list of those, one per row, for at most `target_display_limit` rows**, 10
by default: the first that many in the set's own order, read by one query
bounded by a `LIMIT`, and presented by one call of the presenter's
`presented_rows()`. `target_count` still counts every row, so a template can
say how many it left out. `as_view()` refuses a `target_display_limit` that
is not a positive int, a bool included.

**`as_view()` refuses a page displaying its target**, when the URLconf is
imported, where its spec has no Output, where the Output marks none of its
own fields - choices alone are not markings, and nor is a marking only inside
a nested object - and where the kernel's projection refuses the markings,
such as two fields marked `LABEL`. What it cannot check is whether the
Output describes the target's rows, since that is known only once one is
presented: an Output that does not raises on the page's first request. That
is the cost of turning the display on. A bulk operation whose service returns
something else re-reads its rows with an `output_selector_spec`, as
`publish_drafts_spec` does. The raw `target` stays beside the display, for a
template that wants both.

## The confirmation page

**A service that finds it needs one more value asks for it** by raising
[`AdditionalInputRequired`](https://artui.github.io/django-service-specs/dispatching/)
with a `schema` keyed by the argument it wants back, and the page renders
that question: the form again, bound to every value the post carried, with
the field that answers it revealed. The answer comes back as an ordinary
field of the next post, so there is nothing to hold between the two and no
session to resume.

```python
--8<--
docs/examples/spec_form.py:confirm
--8<--
```

**The answer is a field the spec's form declares**, here `confirmed`: an
optional `BooleanField`, whose box left unticked cleans to `False`. The page
builds no widget from the schema, because the closed argument set refuses a
key no parameter declares, so a control the form does not declare could
never carry the answer. **`asked_fields` holds the field back** until a
question names it. It is taken out of the form every GET and every other
refusal renders, so `{{ form }}`, or a template rendering fields one by one,
shows no box, and a box never shown posts nothing: the service reads
`False` and asks. `as_view()` refuses `asked_fields` unless it is a tuple
of the form's own fields, each optional, since a field held back is absent
from every post no question asked for, none the form disables, since a
disabled field is never read from a post, and none a `server_arguments`
binding answers for. A held field a refusal's error names is shown with its
error.

The URLconf routes it as `books/discard-drafts/`, with `display_target=True`
and `asked_fields=("confirmed",)`, through this template and a page that
includes it, named as the `fragment_template_name`:

```html
--8<--
tests/templates/spec_confirm_form.html
--8<--
```

**What a post that is asked gets back:**

- The kernel's
  [`check_requested_input`][django_service_specs.dispatch.check_requested_input.check_requested_input]
  reads the schema first, with the request's seeds. A question no route could
  hear answered - a key no parameter declares, one a seed fills, a shape a
  transport cannot render, a type the parameter would refuse - raises
  `ImproperlyConfigured` out of the view, on the page and in the fragment
  alike, rather than being rendered. It is the service's mistake, and the
  JSON views raise it too.
- The page answers **422**, the map's status, and the fragment **200**, in
  place: the form's own target, with no `HX-Retarget` or `HX-Reswap`.
- The form is re-rendered with the boxes the schema names revealed, every
  other value as it was posted, and the message among its non-field errors,
  as any other refusal's. A box asked for again after a yes is shown empty,
  so one click never answers a question nobody read.
- The context carries `requested_input`, absent on every other answer:

| Key | Holds |
| --- | --- |
| `requested_input.message` | the refusal's message |
| `requested_input.schema` | the schema it asked under, or `None` |
| `requested_input.fields` | the form's bound fields the schema names, in its order |

A key the schema names that no field of the form declares, such as a
parameter the spec reads from the route, is asked by its message alone.

**The page that asks counts again.** On a bulk page the set is counted for
the post that asked, after the service's pass, so `target_count` and
`target_counted_at` say what the confirming post will find as of then, not
what the GET counted: above, a draft added between the page and its post
makes the question say "3 drafts". The confirming post re-runs the service
on the set as it is when it arrives, and is asked again if the service
finds a reason to.

### A confirmation that lands elsewhere

A form in a table row whose question needs a dialog puts the dialog in the
`spec-form` fragment as an out-of-band element, which htmx swaps into the
element of the same id wherever it is on the page, beside the form swapped in
place. The view's Python does not change, and it sets no header:

```html
--8<--
tests/templates/spec_confirm_dialog.html
--8<--
```

The URLconf routes it as `books/discard-drafts/in-a-dialog/`. Every field of
the form but the ones asked for is rendered in the form, and the dialog
carries the message, the box and a button. **They sit outside the form, so
each names it with HTML's `form` attribute**, which is what makes them post
with it; a control outside the form with no `form` attribute posts nothing.
The page renders the empty dialog only before a question, so whichever way
the page is rendered there is one element with its id. An id that matches
no element on the page swaps nothing.

### Not a gate on an agent route

**The question is a page's affordance, never a security boundary.** On an
agent route, the same `AdditionalInputRequired` goes back to the model as a
retry naming the missing argument, and the model can answer `confirmed: true`
itself on its next call, with nobody asked. So nothing that must have a
person's yes may rely on this question. For a human gate on an agent route,
enable `ToolGuard` in django-spec-agents, whose approval card a person
answers before the tool runs, and keep the spec's tool out of its `exempt`
list.

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
  [below](#an-operation-that-is-unavailable), the form's
  [`form_kwargs`](#a-form-that-takes-keywords), and dispatch. Seed what the
  request carries, never the request itself. **The class-level check is asked
  with them too**: the page asks `has_permission` before it reads anything
  else, through the kernel's `authorize`, which resolves the seeds the check
  names, so a check reading a tenant admits the principal on the page as
  dispatch would.
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

**A named seed is resolved again at each step that names it**, because the
`Grant` a step hands on carries no values. On a POST, the class-level check,
the target's lookup and dispatch each resolve it, and the form's keywords too
where `form_kwargs` names it; on a GET, the affordances' pool takes
dispatch's place, and a [target's display](#the-targets-display), where the
page shows one, is presented with that same pool. A refused post that
presents a display resolves it once more, for the presenter's. So a resolver answering a fresh object answers each step a
different one. Write seed resolvers that answer the same each time.

**One call context per request.** The page creates one plain dict for each
request, starting with `request`, this request, and `view`, this page, and
hands that same object to every kernel call it makes: the
class-level check, the target's lookup and its object-level or scope check,
the affordances, the form's `form_kwargs` and dispatch. So a check, a
condition, `form_kwargs` or the service declaring
[`call_context`](https://artui.github.io/django-service-specs/dispatching/)
reads what an earlier stage of the same request put there, and each request
gets a dict of its own. A selector, a check, a condition, a seed's resolver,
`form_kwargs` or the service naming `request` or `view` is handed the one in
it, as on [the JSON views](http.md#what-the-request-carries), and the
display's presenter reads the page's seeds as its ambient, never the
context.

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

The URLconf routes it as `branches/<branch>/books/new/`. `AtABranch`, a
class-level check, names the `branch` seed, so a branch it does not know is
`PermissionDenied` on GET and on POST before the form is built. Its status
renders disabled with `draft` selected, and a posted status is dropped: every
book it adds is a draft.

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
the target's lookup and once for the affordances' pool, and once more for
each of the class-level check and the form's `form_kwargs` that
[names it](#what-the-request-carries). Write conditions and seed resolvers
that may be asked more than once and answer the same.

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
any Django. That is how the [shipped page](#the-pages-template) carries its
form: an htmx request to a page naming no template is answered with
`django_spec_html/spec_form_fragment.html`, with nothing to configure on any
Django.

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
outcome. One header is read before dispatch, `HX-Trigger-Name`, and only to
stop a post committing on a page that names its
[commit button](#a-post-that-does-not-commit).

<a id="denials"></a>
**A denial is the host's to answer, for htmx too.** The view raises
`PermissionDenied` and `Http404` in both modes, on every page, and a list
row's or a confirmation's denial is answered by the same two handlers. A
handler answering an htmx request with a bare 403 or 404 shows nothing, since
htmx swaps no 4xx. htmx acts on `HX-Redirect` and `HX-Refresh` whatever the
status, so a handler can keep the status and still send the visitor somewhere:

```python
--8<--
docs/examples/htmx.py:denials
--8<--
```

Defined in the root URLconf as `handler403` and `handler404`, they answer as
follows:

| | Without htmx | With htmx |
| --- | --- | --- |
| `handler403`, anonymous | a redirect to the login page, back to the page asked for | `HX-Redirect` to the login page, back to the page the form is on |
| `handler403`, signed in | Django's 403 page | `HX-Refresh` |
| `handler404` | Django's 404 page | `HX-Refresh` |

**The reload answers with the 403 page only where the page itself refuses on
GET**: the spec's class-level check, or the object-level check on the row the
route names. A denial only the operation raises, from inside the service, does
not recur on GET, so the reload shows the form again, empty, and what was typed
is gone; a host whose services refuse that way answers those in the form
instead, by raising a `ServiceError`. A 404 under htmx is usually a row that
went away since the page was drawn, and the reload shows the page without it.
The view's `Vary` never reaches a denial, which is raised rather than
answered, so each handler sets its own on `HX-Request`, since it answers the
two kinds of request differently.

**The pages are sync views and serve under ASGI.** Django runs a sync view on a
thread there, so a project on an ASGI server needs nothing of the page. There
is no async page: its template renders synchronously, and a lazy row or a
relation read there would raise `SynchronousOnlyOperation` on the event loop,
so an async page would hop to a thread for the render anyway, which is the hop
Django already makes. `AsyncSpecView` differs because it answers JSON presented
inside `adispatch`, with no template.

## A post that does not commit

A form that refreshes a dependent choice, adds a row or searches posts without
meaning to run the operation. **`commit_button` names the submit button whose
post commits**, and every other post re-renders the form. It is `None` by
default, and then every post commits, as a page that does not set it always has.

```python
--8<--
docs/examples/spec_form.py:refresh
--8<--
```

The URLconf routes it as `editions/new/`, with `commit_button="add"`, and its
form is a file of its own, so it renders on any Django:

```html
--8<--
tests/templates/spec_edition_form.html
--8<--
```

Choosing an author posts the form, through htmx where it is loaded, and
"Show their books" does the same without it. Neither carries `add`, so
neither runs the operation; the form comes back with the books of the author
posted, and "Add the edition" commits. A book by another author is refused on
that post, since the Validator's form is narrowed by the same post.

**Which posts commit**, decided by one method, `commits(request)`, to
override for another rule, such as two buttons that both commit:

- **The post's data must carry the button's name.** A browser posts the name
  and value of the submit button that was pressed and of no other, with or
  without JavaScript. htmx does the same for a form it submits, and posts no
  button's name for a request a control issues itself, such as the author
  picker's `hx-post`.
- **Under htmx 2, `HX-Trigger-Name` can stop a commit, never make one.** It
  names the element the request came from: the form, whose `name` is never
  posted, or a control, whose own value always is. A header naming a key the
  post carries, other than the button, is another control's request, and does
  not commit even where the button's name reached the data some other way,
  such as an inherited `hx-vals`. A form's own `name`, or no header at all,
  leaves the decision to the data, which is what decides for every client:
  a browser and htmx alike post the pressed button's name.
- **Enter presses the first submit button** in the form, so put the commit
  button first, as the template above does; a refresh button placed before it
  would turn Enter into a refresh. Give a refresh button `formnovalidate`, so
  the browser posts the form with its required fields still blank.

**What a post that does not commit renders** is the page GET renders, with
the form bound to the post:

- It is admitted and finds its target as GET does, so a principal the spec
  refuses is refused, and the context carries `target`, the count of a set,
  `target_display` and `unavailable` as GET's does.
- The form is built through `form_kwargs` like every other form the page
  builds, bound to the post, so a choice its constructor narrows - by a
  keyword, or by a value posted - is narrowed again.
- Nothing is dispatched and nothing validated: the form shows the values
  posted and no error, and a required field left blank is not called
  required. `asked_fields` are held back, as on GET.
- The context carries `refreshed`, `True`, for a template that needs to tell;
  it is absent from every other render.
- The page answers 200, and an htmx request the form's fragment at 200.

`commit_button` is checked at `as_view()`: `None`, or a non-empty string that
names no field of the form, since a field is posted by every submit and could
not say which one commits.

## A success message

**`success_message` is sent through `django.contrib.messages` on every
success**: the page's redirect, the `HX-Redirect` and the result fragment
alike. It is formatted as Django's `SuccessMessageMixin` formats its own, `%`
with the form's cleaned data, so `"Edition %(number)s added."` above reads the
number posted, as the form cleaned it. The cleaned data is the result's
`data`, what the Validator's form handed the service; on a
[partial page](#a-partial-page) a field left blank is not in it.

`get_success_message(result)` answers it, to override for a message that reads
what the operation returned:

```python
class AddBookThenSayWhich(SpecFormView):
    def get_success_message(self, result: DispatchResult) -> str:
        return f"Book {result.value.pk} added."
```

- **Without the messages middleware it is skipped**, and never raises
  Django's `MessageFailure`: a message needs the store the middleware puts on
  the request, which the app installed alone does not, and the write has
  already committed.
- **A refusal, a confirmation page and a post that does not commit send
  none.** The message is sent once the operation has run.
- An empty message, the default, sends nothing.
