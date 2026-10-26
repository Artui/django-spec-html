# Serving a formset

A `many` spec takes a list of rows under its `many_argument`, and the kernel
validates the call **per row**: the shape check and the Validator run on each
row, every failing row is refused at once, and each refusal is addressed by the
row's index. `SpecFormSetView` serves one as a page: a formset of the spec's
form, one form a row, posted as the list. The [single-form page](pages.md)
refuses a `many` spec, since one form posts one row; this is the page that
serves it. The [request's hooks](pages.md#what-the-request-carries), the
[htmx fragment](pages.md#an-htmx-request), [a post that does not
commit](pages.md#a-post-that-does-not-commit) and the denials are that page's
own code; this page describes what differs, which is the form, the reading,
the refusal, the scope check and [the success message](#a-success).

```python
from django_spec_html import SpecFormSetView
```

The examples on this page add several books in one call. The spec's
Validator is a [`FormValidator`](https://artui.github.io/django-service-specs/forms/)
of a row's form, and the spec is `many`:

```python
--8<--
docs/examples/spec_form_set.py:spec
--8<--
```

```python
--8<--
docs/examples/spec_form_set.py:urls
--8<--
```

The template renders the formset inside a POST form with its token. Render
`{{ formset.management_form }}` as well as the forms, or every post is
refused, and `{{ formset.non_form_errors }}`, where a refusal of the list as a
whole is placed:

```html
--8<--
tests/templates/spec_formset_form.html
--8<--
```

**A template is optional.** A view naming none renders
`django_spec_html/spec_form_set.html`, which this package ships, with its
formset in `django_spec_html/spec_form_set_fragment.html`, the file an htmx
request is answered with. It is found, refused at `as_view()` when no engine
finds it, and overridden as [the form page's is](pages.md#the-pages-template):
list `django_spec_html` in `INSTALLED_APPS` with `APP_DIRS` on. It renders,
escaped and with no stylesheet, script or htmx attribute, the management
form, the non-form errors - a question's message among them - and each form
as a fieldset of its own with its errors, then the submit button, posting
`commit_button`'s name where one is set, and the `add_button`, which skips the
browser's checks, since its post commits nothing. It has no target pane: a
`many` spec has no target.

## The formset

**GET** renders `template_name` with an unbound formset under `formset`, and
`view` and `spec` beside it, once the principal is admitted: an anonymous
visitor above gets Django's own `PermissionDenied`.

- **Admission asks the scope check of nothing.** There is no target to look
  up, so after the spec's class-level check the page asks the scope check of
  `None`, as dispatch asks it of every `many` call, and a principal the post
  would refuse is never offered the page. A route value the spec's `reads`
  refuse is a 400, as on every page. A post asks it **once**: dispatch asks
  it, and where the page's own reading refuses the post first - its
  management form, a row's coercion - the page asks it before answering, so
  a principal it refuses is denied rather than shown the formset, as the
  single page denies one before reading its form. A route value the `reads`
  refuse leaves it unasked, as on the single page: there are no arguments to
  ask it with.

- **`extra` blank forms**, 1 by default, after the spec's `many_min_length`
  forms, which a person must fill in. The page above offers three.
- **Never more forms than the spec takes.** The formset's `min_num` and
  `max_num` are the spec's `many_min_length` and `many_max_length`, never
  attributes of the view, so no second declaration can drift from the first.
  The formset validates neither: the list's length is the kernel's to refuse,
  in its words.
- **Every form is built as the Validator builds a row's**, with what its
  `form_kwargs` answers, through the kernel's `form_keywords`, as
  [the single page builds its form](pages.md#a-form-that-takes-keywords): asked
  once per request, with the principal, the request's seeds and its call
  context. A `many` spec has no target to hand it, so the answer is the same
  for every row.
- **No target pane.** A `many` spec declares no target selector - the kernel
  refuses one when the spec is built - so the context carries no `target`,
  `target_count` or `target_display`, and `display_target` is refused at
  `as_view()`.

## The post

**Each form is read as the single page reads its form**: through its own
widgets, a blank field absent, a disabled field not read, a date in the
form's input format sent in the form the shape check reads, and a choice's key
coerced against the row's own declaration. The rows travel in the formset's
order as one list under `many_argument`, and the service receives it once.

- **A form left as it came sends no row.** An extra form a person did not
  touch is not sent, so the page above, posted with its middle form blank,
  adds two books, not three. The first `many_min_length` forms are sent
  however they are left, and the kernel refuses a blank row there as it
  refuses any row missing a required field.
- **The management form is never an argument.** Its keys - `TOTAL_FORMS`,
  `INITIAL_FORMS` and the rest, under the formset's `form-` prefix - are the
  formset's own, as the CSRF token is the page's. A post missing them, or
  carrying ones Django refuses, is answered at 400 with Django's own message
  as a non-form error, and nothing is dispatched: a formset without its
  management form reads no forms, and would send an empty list.
- **The route's kwargs are the call's**, read and coerced once beside the
  list, never inside a row, and checked against the spec's top level: a
  `many` spec's `reads`.

## A refused post

A refused post re-renders the formset at the status the
[single page answers it with](pages.md), from the [one map](http.md#refusals), and the refusal is split
back onto the forms:

- **A refusal of row *i* lands on the form that sent it**, through
  `add_argument_errors`, so a form left blank and dropped shifts nothing: on
  the page above, a bad price in the third form, posted beside a blank second
  one, is the list's row 1 and is placed on the third form.
- **A refusal of the list as a whole lands on the formset's
  `non_form_errors()`**: its length - six books where the spec takes five -
  and a message the Validator gives about the rows together.
- **A refusal of a route value** is a non-form error too, prefixed with its
  name, `"shelf: Enter a whole number."`, in the same post as the rows'.
- **Every row's own checks answer in the same post.** Beside an
  `InvalidArguments`, each sent form is validated as the Validator validates a
  row's, so a row the shape check refused - a required field left blank -
  shows its form's further checks, a `clean()` among them, beside it. On a
  `partial` spec the forms carry the refusal alone, as
  [a partial page's form does](pages.md#a-partial-page).
- **Any other refusal** - a conflict, a service's own rule - is its message as
  a non-form error, and no form is validated again.

**A question is answered by its message alone.** An
`AdditionalInputRequired` is checked by the kernel's `check_requested_input`
first, as on every page, so a schema no route could hear answered - a key
declared only inside a row among them - raises `ImproperlyConfigured` and is
never rendered. Then the formset is re-rendered at 422 with the message as a
non-form error and `requested_input`, its `message` and `schema`, in the
context, with no `fields`. A `many` call's top level is the list and the
spec's `reads`, which no form declares, and the page builds no widget from a
schema, so a confirmation asked once for the whole call is not built on this
page.

## Adding a row

**`add_button` names a second submit button whose post is the page again with
one more form**, bound to everything posted, and nothing dispatched. It needs a
`commit_button`, since without one every post commits, the add button's
included. The page above names both: "Save" runs the operation, and "Add a row"
offers a fourth blank form with the first three as they were filled in. At the
spec's `many_max_length` it offers none, since the list takes no more. Every
other post that does not commit is the page again with the forms it posted,
validated by nothing, as on [the single page](pages.md#a-post-that-does-not-commit).

## A success

A success redirects to `success_url`, or answers `HX-Redirect` or the result
fragment to htmx, as on the single page. **`success_message` is formatted
with the rows' `count`**, `%` with `{"count": n}`, so `"%(count)d books were
added."` above says how many: the single page formats with the cleaned data,
and a `many` call's is a list, which `%` with a name cannot read. Override
`get_success_message(result)` to read the rows, under `result.data`, or what
the operation returned, under `result.value`.

## What `as_view()` refuses

When the URLconf is imported: a spec whose Validator is not a `FormValidator`;
a spec that is not `many`; a view displaying its target; an `extra` that is
not a non-negative int; an `add_button` that is not a button's name, is set
without a `commit_button`, or is the `commit_button`; and either button named
under the formset's `form-` prefix, which every post carries. Then every check
the single page makes of the request's hooks. The view is **sync only** in this
release.
