# Confirming an operation

A spec with nothing for a person to fill in, served as the page that confirms
it: what the operation acts on, and a button. Withdrawing a book, archiving a
note, closing every stale ticket. Every argument the operation takes comes
from the server's side - the route, a pool seed, a `kwargs` provider or
`server_arguments` - so the page posts nothing but its token and the button,
and **nothing a post carries becomes an argument** but the selection a bulk
action names in [`posted_arguments`](#a-bulk-action-over-ticked-rows).

`SpecConfirmView` serves a spec whose Validator is `None`. It shares
everything with [`SpecFormView`](pages.md) but the form: the admission, the
target and its display, the affordances, the fragment wrapper under htmx, the
post that does not commit, the success and its message. Each works as
[Serving a page](pages.md) describes it, and this page says only where the
two differ.

```python
from django_spec_html import SpecConfirmView
```

## Which view serves a spec

| The spec's Validator | The view |
| --- | --- |
| a `FormValidator` | `SpecFormView`: the page is the form |
| `None` | `SpecConfirmView`: the page is the target and a button |
| any other | neither: a page renders no inputs it would have to guess |

**Use `SpecConfirmView` when there is nothing to ask a person but yes.** If the
operation takes a value a person chooses - a reason, a date, a "yes, for
good" box - declare a form that holds it and serve the spec with
`SpecFormView`, whose `asked_fields` holds a confirmation back until the
service asks for it. `as_view()` refuses, when the URLconf is imported, a
spec with a Validator: a `FormValidator`'s is pointed at `SpecFormView`, and
any other is named.

The example withdraws a published book. The route names the book, and there
is nothing else to send:

```python
--8<--
docs/examples/spec_confirm.py:spec
--8<--
```

```python
--8<--
docs/examples/spec_confirm.py:urls
--8<--
```

## What the page renders

**GET** renders `template_name` with the context every page builds - `view`,
`spec`, `target`, a set's `target_count` and `target_counted_at`,
`target_display` on a page that sets `display_target`, and `unavailable`
when an affordance is not met, or on a page serving `actions`
`unavailable_actions`, `refused_actions` and `action_buttons`, each action's
own answer - **and
no `form`**. A key with nothing to
hold is absent, as on every page, so a template's `{% if form %}` is false
rather than `{{ form }}` rendering `None`. The template renders its own
`<form method="post">`, the CSRF token and the submit button:

```html
--8<--
tests/templates/spec_confirm_view_form.html
--8<--
```

The URLconf above renders it as the fragment under htmx, and inside this page
otherwise:

```html
--8<--
tests/templates/spec_confirm_view.html
--8<--
```

Above, the page names the book by its `LABEL`. On a draft the button is
disabled, with the affordance's reason above it.
Dispatch still asks every affordance on the post, so a book withdrawn between
the page and its post is refused at 409.

### The shipped template

**A template is optional.** A view naming none renders
`django_spec_html/spec_confirm.html`, which this package ships, with its
target pane and button in `django_spec_html/spec_confirm_fragment.html`, the
file an htmx request is answered with. It is found, refused at `as_view()` when
no engine finds it, and overridden as
[the form page's is](pages.md#the-pages-template): list `django_spec_html` in
`INSTALLED_APPS` with `APP_DIRS` on. It renders, escaped and with no
stylesheet, script or htmx attribute:

- the target pane: a set's count and when it was counted, and
  `target_display` where the page sets `display_target`, but never the target
  itself;
- an unmet affordance's `reason`, with the button disabled;
- a refused post's `errors` as a list, or a question's message alone, since no
  control is built from its schema; a refusal's `code` is the form's
  `data-code`;
- the button, posting `commit_button`'s name where one is set.

## What a post sends

**POST dispatches with the route's kwargs alone**, read by `route_kwargs` and
coerced by their declarations, with the request's seeds and the binding laid
over them, as a form page's are. The post's own data is never read but for
the names `posted_arguments` lists: not the CSRF token, not the button, not
any other key named like a parameter. A post carrying
`pk=7` to `/books/3/withdraw/` withdraws book 3. So the closed argument set
holds with nothing to check, and a post cannot fill a parameter the route, a
seed or `server_arguments` fills, nor one nobody declared.

`commit_button` works as on a form page: above, only a post carrying
`withdraw` runs the operation, and any other post is the page again, with
`refreshed` in its context. A page with [`actions`](pages.md#one-form-several-operations)
takes no `commit_button`: `as_view()` refuses the pair, and an action keyed like a
`posted_arguments` name or `csrfmiddlewaretoken`, which every post carries.
**Each action's button answers its own availability**: the shipped template
disables an unavailable action's button and shows its reason, the others stay
usable, and a post naming the unavailable one is refused at 409, as
[on a form page](pages.md#one-form-several-operations). An action after the
first that the user may not run - its permissions refuse them, or its scope
check refuses the target - is disabled with no reason and no code, its key in
`refused_actions`, and a post naming it is refused, 403 or 404, as before.
A refused post's page keeps each action's state, asked of the target that post
found, as [on a form page](pages.md#one-form-several-operations).

### A bulk action over ticked rows

A list whose rows carry checkboxes and whose one button archives the ticked
ones posts the selection itself: the rows are the person's choice, not the
route's. **`posted_arguments` names the parameters a post supplies**, and
nothing else the post carries is read. Here `archive_notes_spec` reads
`Parameter("ids", "array", items="integer")` and archives those notes:

```python
from django.urls import path, reverse_lazy

urlpatterns = [
    path(
        "notes/archive/",
        SpecConfirmView.as_view(
            spec=archive_notes_spec,
            success_url=reverse_lazy("notes"),
            posted_arguments=("ids",),
        ),
        name="archive-notes",
    ),
]
```

The list renders a checkbox per row and a button that posts them with
htmx's `hx-include`, so the boxes need no form of their own:

```html
<table>
  {% for note in notes %}
    <tr><td><input type="checkbox" name="ids" value="{{ note.pk }}"></td><td>{{ note }}</td></tr>
  {% endfor %}
</table>
<button hx-post="{% url 'archive-notes' %}" hx-include="[name=ids]:checked"
        hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}' name="archive">Archive</button>
```

- **Each name is read as a query string is**, by the kernel's
  `flat_arguments`: every value of an array parameter, so `ids=3&ids=5` is
  `[3, 5]`, a comma list split (`ids=3,5`), the last value of anything else,
  and a blank absent, so a post with nothing ticked carries no `ids`. It is
  then merged with the route's arguments and coerced by its declaration.
- **Any other posted key never reaches the service**, a parameter of the spec
  included, so the closed argument set stays closed.
- **A refused value is a refused post**: `ids=x` answers 400 with
  `"ids.0: Enter a whole number."` in `errors`, as does a list the shape
  check refuses (too short for a `min_length`), or under `refusal_redirect`
  that message and a redirect back to the list. It is never a 404:
  `route_refusal_404` is the route's alone.
- **`as_view()` checks the names**: each must be a parameter of the spec, and
  none may be one `route_kwargs` fills from the URL, since a parameter is
  the route's or the post's, never both. `posted_arguments` must be a tuple.

## Every required parameter has a source

**`as_view()` refuses a spec demanding a parameter that nothing but a post
could supply**, since no post to this page carries one unless
`posted_arguments` names it. Which parameters the call
demands is the kernel's answer, read through `bind_arguments` on a call
carrying nothing: a parameter that is optional or declares a default is not
demanded, and nor is a key a typed `kwargs` provider fills or may decline,
or any read an untyped provider reaches. Each one left must be supplied by one of four
sources, as they stand when the URLconf is imported:

| Source | Supplies |
| --- | --- |
| the route | each parameter `route_kwargs` names a kwarg into: a tuple's names, a mapping's targets |
| the seeds | each name the kernel's `server_owned_keys` reads off the declared `pool_seeds`, such as a `reflected` read a registered seed fills |
| the binding | each name `server_arguments` binds a value to |
| the post | each name `posted_arguments` lists |

**The route must say what it fills.** `as_view()` is called while the
URLconf is being built, before the pattern it is mounted on exists, so under
the strict rule, `route_kwargs = None`, the route supplies nothing to this
check. That is why the example names `route_kwargs=("pk",)`. A mapping counts
only the parameters it maps a kwarg into, so under `{"org": None}`, for an
`include()` prefix, a `pk` the route still passes under its own name is not
counted until it is mapped to itself: `{"org": None, "pk": "pk"}`. Without
any:

```text
SpecConfirmView requires pk, which no post to a confirmation carries: fill each from the route, naming it in route_kwargs, or from a pool seed, a kwargs provider or server_arguments, or name it in posted_arguments.
```

A seed only `get_pool_seeds` adds per request is not seen either, since
there is no request yet: declare it on `pool_seeds`. A name `server_arguments`
withholds is refused by the kernel's own `check_server_arguments` first, in
its words.

## A refused post

**A refused post is the confirmation page again**, at the status of the one
map, and needs no messages app: a button posted from another page lands on a
page that works, and says why.

- The page answers 400 for an argument refusal, 409 for a conflict or an
  unavailable action, 422 for any other service refusal, and under htmx the
  fragment at 200, as on every page.
- The context carries `errors`, every message the refusal holds, as a list of
  strings in the refusal's order. With no field to put one on, a message
  about a parameter is prefixed with its path: with `route_refusal_404` off,
  a route value the target's reads refuse is `"pk: Enter a whole number."`
  (on, it is `Http404`).
- `code` is there for a refusal carrying one, as on a form page, and the
  target is shown again.
- On a page serving `actions`, `unavailable_actions`, `refused_actions`,
  `action_buttons` and `unavailable` are there as GET puts them, asked of the
  target the post found, so a button GET disables stays disabled.
- A denial is raised for the host's 403 or 404 page.

A confirmation's denial is the host's to answer, for htmx too, by the
handlers in [a denial under htmx](pages.md#denials).
## A refusal sent back

A button in a list's row is posted from the list, and the page a refusal is
rendered on is one the person never opened. **`refusal_redirect=True` sends
the refusal back instead**, to the page it came from. The URLconf routes the
example as `books/<int:pk>/withdraw/from-the-list/`, with
`redirect_field_name="next"` beside it, and a row's form posts to it with
`?next=` naming the page of the list it sits on:

```python
--8<--
docs/examples/spec_confirm.py:from-the-list
--8<--
```

- **An invalid, a failed or a needs-input refusal is sent back.** Each
  message the page would have shown in `errors` is sent through
  `messages.error`, and the refusal's `code`, where it has one, is the
  message's `extra_tags`, so the list can style or match an unavailable
  action: a book withdrawn already is sent back with "Only a published book
  can be withdrawn." tagged `not_published`.
- **The post redirects to `get_refusal_url(refused)`**: the safe URL
  `redirect_field_name` reads, checked as
  [a form page checks it](pages.md#returning-where-the-page-was-opened-from),
  and otherwise `success_url`. Override it to read the refusal, such as to
  send one `code` to a page of its own. Under htmx it answers `HX-Redirect`
  to the same URL, since an XHR would follow a 302 and swap the list into the
  row.
- **A denial is raised as ever**, for the host's 403 or 404 page.
- **Without the messages middleware the page is rendered as above.** Django
  raises `MessageFailure` with nowhere to keep a message, before any is
  stored, so the page renders the refusal itself rather than lose it in a
  redirect. A refusal with no message to send - an empty detail, or an empty
  message, which Django's storage drops - is the page again too.
- **A refusal sent back reads nothing for the page.** The redirect is decided
  before the target is shown again, so a set is not counted and no presenter
  is asked.

`as_view()` refuses `refusal_redirect` on a page with no `success_url` that
does not override `get_refusal_url`, since a refused post naming no safe URL
would have nowhere to go. A form page has no such switch: a refused form
redirected away would lose what the person typed.

## A success

A success redirects to `get_success_url(result)`, or under htmx answers
`HX-Redirect` or the `spec-result` fragment, as on a form page. Where the view
names `redirect_field_name`, a safe URL the post or its query carries under
that key is where the success goes instead, as on
[a form page](pages.md#returning-where-the-page-was-opened-from): the example
above returns to `/books/?page=3`. Nothing the post carries is an argument, so
the key never reaches dispatch.

**`success_message` is formatted with the target**, `%` with a mapping whose
one key is `target`, `result.instance`: `"%(target)s was archived."` names
the row by its `str()`. A form page formats with the cleaned data, which with
no Validator is `{}`. A set is the queryset, read when the message is
formatted, so it holds the rows that still match once the operation has run.
To name what the operation returned, override `get_success_message(result)`
and read `result.value`. The message is sent where the messages middleware
runs, and skipped where it does not.

## A question cannot be answered here

**A service raising `AdditionalInputRequired` is shown its question, and the
page cannot answer it.** The schema is checked first by the kernel's
`check_requested_input`, as on a form page. Then the page is re-rendered at
422, or as the fragment at 200, with `requested_input` - the `message` and
the `schema` - and the message in `errors`.

The page builds no control from a schema, and no post is read for an answer,
so a confirming post is asked again. With no Validator, an answer could only
be a top-level parameter the spec `reads`, and reading one off the post would
undo what the page is for:

- every post carries the CSRF token and the button, so refusing undeclared
  keys needs a list of exemptions, and a box with no `value` posts `"on"`,
  which a `boolean` parameter refuses;
- the template would have to build the control from the schema, which no page
  does: on a form page, the form declares it;
- and the post would become a channel filling a read on every post, not only
  the one a question asked.

So **an operation that asks a person something mid-flight declares a form
that holds the answer**, an optional `BooleanField`, and is served by
`SpecFormView` with that field in `asked_fields`, as
[the confirmation page](pages.md#the-confirmation-page) shows. A page with a
target and a button is already the confirmation: a service that also asks on
this route will ask forever.

### Not a gate on an agent route

**Neither the page nor a question is a security boundary.** The spec is one
declaration for every route, and on an agent route the same
`AdditionalInputRequired` goes back to the model as a retry naming the
missing argument, which the model can answer `confirmed: true` itself on its
next call, with nobody asked; the page's button is no gate there either,
since an agent calls the operation without it. So nothing that must have a
person's yes may rely on either. For a human gate on an agent route, enable
`ToolGuard` in django-spec-agents, whose approval card a person answers
before the tool runs, and keep the spec's tool out of its `exempt` list.
