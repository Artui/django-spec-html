# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **A page's template is optional.** `SpecFormView`, `SpecFormSetView` and
  `SpecConfirmView` default `template_name` to a template this package ships:
  `django_spec_html/spec_form.html`, `spec_form_set.html` and
  `spec_confirm.html`, each extending `django_spec_html/base.html`. They are
  plain, escaped documents with no stylesheet, script or htmx attribute. They
  render the target pane, an unmet affordance's reason with the form disabled,
  the form or formset with its errors, a question's message, a refusal's
  `code`, and a submit button posting `commit_button`'s name. Each page's form
  is a file of its own (`*_fragment.html`), which an htmx request is answered
  with on every Django. The templates are found as any app's are, so list
  `django_spec_html` in `INSTALLED_APPS` with `APP_DIRS` on; `as_view()` raises
  `ImproperlyConfigured` when the view's engine finds no page or fragment it
  would render. Override any of them the
  Django way, or set `template_name` as before.
- **`SpecFormSetView` serves a `many` spec as a formset of its form**: each form
  a row, sent as one list under `many_argument`, a form left blank sending no
  row, and a refusal of row *i* placed on the form that sent it. The page shows
  the spec's `many_min_length` forms, which are always sent, then `extra`
  blank ones (1 by default), never past `many_max_length`; with
  `commit_button` set, `add_button` names a second submit that re-renders the
  formset with one more form and dispatches nothing. Its scope check is asked
  of no target, on GET and before any post refused before dispatch reaches it
  shows its forms - one the page's reading refuses, or a list the shape check
  finds too long or too short - so a principal it refuses is denied, and
  `as_view()` refuses a formset page displaying a target, an `extra` that is
  not a non-negative int, and an `add_button` that is not a button's name.
- **`SpecConfirmView` serves a spec with no Validator** as a page showing its
  target and a submit button, with nothing posted becoming an argument. A
  refused post re-renders the confirmation page at the refusal's status, with
  its messages as `errors`. A question is shown by its message, and is answered
  on a form page instead. `as_view()` refuses a spec with a Validator, and a
  required parameter that the route (named in `route_kwargs`), a declared seed,
  a provider or the binding does not supply. `success_message` is formatted
  with the target.
- **`SpecFormView` renders a service's `AdditionalInputRequired` as a
  confirmation page.** The refusal's schema is checked first by the kernel's
  `check_requested_input`, with the request's seeds, and a question no route
  could hear answered raises `ImproperlyConfigured` on the page and in the
  fragment alike. Otherwise the bound form is re-rendered with every value
  posted, at 422 on the page and at 200 in place as the fragment, with no
  `HX-Retarget` or `HX-Reswap`, and the context carries `requested_input`: the
  message, the schema and the form's bound fields it names. The answer is a
  field the spec's form declares, and the new `asked_fields` attribute, checked
  at `as_view()`, holds those fields back from every GET and every other
  refusal until a question names them, and shows them empty when asked. The
  docs show the question landing in an out-of-band dialog, and say why it is no
  gate on an agent route.
- **The page shows its target beside the form.** On GET and on every refused
  post it re-renders, the context carries `target`: the row an instance
  selector found, or the set a `collection_selector_spec` resolved. It is
  absent for a create and for a missing row `allow_none` lets through.
- **A bulk page counts its set and says when.** `target_count` and
  `target_counted_at` come from one `COUNT` on a fresh clone, never by reading
  the rows, so a template says "117 rows selected, counted at 14:02" rather
  than promising a number. An empty set is counted 0 and is no refusal; its
  scope check is still asked. The confirming post re-runs the service on the
  set it finds, and a successful post counts nothing.
- **`target_display`**, on a page that sets the new `display_target` (off by
  default), is the target projected for a person by its Output's
  `FieldMarking`s, presented through the spec's presenter with the context
  dispatch hands it. `HIDDEN` and `HANDLE` fields are dropped, choices are
  spoken as their displays, and the field marked `LABEL` names the row. A set's
  display covers at most `target_display_limit` rows (10 by default), read in
  one bounded query. `as_view()` refuses a page displaying its target whose
  spec has no Output, marks none of its own fields, or carries markings the
  projection refuses, and a `target_display_limit` that is not a positive int.
- **A post that does not commit.** `commit_button` names the submit button
  whose post commits; any other post - a dependent choice refreshed, a row
  added - is the page again with its form bound to the post, built through
  `form_kwargs`, with no dispatch, no validation and `refreshed` in the
  context, at 200 on the page and in the fragment. Under htmx 2, an
  `HX-Trigger-Name` naming another posted control does not commit either.
  `None`, the default, commits every post as before; `commits(request)` is the
  rule to override, and `as_view()` refuses a name that is not a button's.
- **`success_message`**, sent through `django.contrib.messages` on every
  success - the redirect, `HX-Redirect` and the result fragment - formatted
  with the form's cleaned data as Django's `SuccessMessageMixin` formats it,
  through an overridable `get_success_message(result)`. Without the messages
  middleware it is skipped, never raising `MessageFailure`; a refusal, a
  confirmation page and a post that does not commit send none.
- **`SpecFormView` builds its form with the Validator's `form_kwargs`**,
  through the kernel's `form_keywords`: the principal, the request's seeds, the
  row and one call context per request, which dispatch is handed too. A form
  whose `__init__` takes a keyword is served on GET and on POST.

### Changed
- **The kernel is floored at 0.17.0**, for `form_keywords` and a seeded
  `authorize`.
- **A partial spec's page renders no HTML `required` attribute**, so a person
  can post the subset the spec takes.

### Fixed
- **The page's class-level check is asked with the seeds it names**, through
  `authorize(..., pool_seeds=...)`, instead of raising `TypeError`.
- **A refusal of the arguments shows every error in one post**: the page's form
  is validated under `clean_held_to_their_columns` and its errors are merged
  without duplicates, except on a partial page or a refused route.
- **The JSON views check a service's asked schema against the seeds the call
  ran with.** `dispatch_request`, `adispatch_request` and so `SpecView` and
  `AsyncSpecView` checked an `AdditionalInputRequired` schema with no pool
  seeds, so a key a seed fills - one declared in `pool_seeds` or one
  `get_pool_seeds(request)` adds - was written into a 422 body as a question
  the client could not answer. It now raises `ImproperlyConfigured`, as the
  page half already did.

## [0.2.0] — 2026-10-26

### Added
- **An htmx request to `SpecFormView` is answered with a fragment of the page.**
  The view decides a request's outcome once and then chooses the answer: the
  page, unless `wants_fragment(request)` says htmx asked for less, which it does
  for an `HX-Request` that is neither boosted nor a history restore, read from
  django-htmx's `request.htmx` where its middleware set it and from the headers
  where it did not. The fragment renders `get_fragment_template_names()`,
  which is `fragment_template_name` or, by default, the page's own
  `<template_name>#spec-form`, a `{% partialdef %}` that Django 6.0 resolves;
  below 6.0, django-template-partials 23.3 or later does, with its tag named as
  a `TEMPLATES` builtin and its loader around the project's own. GET answers
  the form fragment, and every refusal the page re-renders answers it at 200
  rather than the page's 4xx, since htmx swaps no 4xx by default. A success is
  `fragment_success(result)`: `HX-Redirect` to `get_success_url(result)`, never
  a 302, encoded and checked as the page's redirect is, so an unsafe scheme
  raises `DisallowedRedirect` in both modes; or the template's `spec-result`
  fragment rendered in place, with `result` in its context, and
  `HX-Trigger: spec-succeeded`. A denial is raised in both modes, for the
  host's handlers. A template with no fragment at the address is Django's
  `TemplateDoesNotExist` naming it, never the page. Every answer varies on
  `HX-Request`, `HX-Boosted` and `HX-History-Restore-Request`, and no htmx
  header reaches dispatch.
- **`Outcome`**, the five ways a page's post ends (`ok`, `invalid`,
  `needs_input`, `denied`, `failed`), exported from the root. The one status
  map gains each refusal's outcome as its third column, so the page and the
  JSON views still read a single table.
- **The request reaches the spec on the JSON views.** `SpecView` and
  `AsyncSpecView` gain two hooks, each defaulting to the attribute of its name:
  `get_pool_seeds(request)`, so a tenant, a locale or a team the request
  carries reaches a permission check, a selector or a Validator; and
  `get_server_arguments(request)`, the kernel's `ServerArguments` the server
  answers for, whose values outrank whatever the client sent. The declared
  `server_arguments` is checked by `check_server_arguments` at `as_view()`,
  against the declared `pool_seeds`; a per-call binding may vary the values,
  and one that binds or withholds other names raises `ImproperlyConfigured`,
  as does a seed `get_pool_seeds` adds per request under a name the binding
  binds or withholds. Every name the server answers for is dropped from what
  the client sent before the request is coerced, so a client's malformed value
  for it is never refused. Both hooks are plain `def`, and `as_view()` refuses
  an `async def` one; `AsyncSpecView` runs them off the event loop. No
  `request` seed is registered: a spec callable requiring one raises
  `TypeError` on every reader that has no request.
- **`route_kwargs` serves a route whose kwargs are not all the spec's**, as an
  `include()` prefix such as `teams/<team>/` is. On both JSON views, and as a
  keyword of `route_arguments`, `request_arguments`, `dispatch_request` and
  `adispatch_request`: a mapping from a captured kwarg to the parameter it
  fills, or to `None` to drop it, or a tuple of names as an allow-list. The
  default is the strict rule, unchanged, and a kwarg a mapping does not name
  is still held to it. On a view, `as_view()` refuses a bare string, and a
  target or allowed name the spec does not declare or the server binding
  answers for, when the URLconf is imported. Two kwargs filling one parameter
  are refused on the request that captures both.
- **A route kwarg that fills a name the server answers for is refused** as
  `ImproperlyConfigured`, naming the captured kwarg and pointing at
  `route_kwargs={name: None}`, which drops it explicitly: the route is the
  host's, and may not contradict the host's own binding.
- `dispatch_request` and `adispatch_request` take `server_arguments=` and apply
  it with `apply_server_arguments` before dispatch; `request_arguments` takes
  it to drop those names from what the client sent before coercion.
- **`SpecFormView` takes the same attributes and hooks as the JSON views**:
  `route_kwargs`, `get_pool_seeds(request)`, `server_arguments` and
  `get_server_arguments(request)`, checked by the same code as `SpecView`'s at
  `as_view()` and on each request. The binding applies wherever the page reads
  arguments: a field of the page's form the binding binds renders disabled with
  the server's value, one it withholds disabled and empty, on GET and on a
  refused post, and neither is read from the post; the server's values are
  laid over the route's before the target is found and over the post's before
  dispatch, so the page's form, the target's lookup and dispatch read the
  server's value. The per-call seeds reach the target's lookup, its
  object-level and scope checks, the affordances and dispatch. The page's
  class-level permission check is still asked with no seed, its own
  `pool_seeds` included, so one naming a seed raises `TypeError` on the page,
  where the JSON views hand it the call's seeds.
- **The page asks a spec's affordances on GET.** Once the target is found, the
  callable conditions are asked through the kernel's
  `unmet_operation_affordance`, on a pool built from the call's seeds, and,
  with none of those unmet and a row to ask about, the conditions on the row
  as dispatch asks them. On a GET with a row, each callable condition is
  therefore asked, and each seed resolved, twice. The first unmet `Affordance`
  is in the context as `unavailable`, in the page and the fragment alike; it
  is absent when every condition is met. The post is unchanged, and
  dispatch's 409 stays the authority.
- **A refused post's context carries the refusal's `code`**, for an
  `ActionUnavailable`, as `error_response` puts it in the JSON body.

### Changed
- **An htmx post to the page is no longer answered as any other.** A success
  is `HX-Redirect` rather than a 302, and a refusal is the form fragment at 200
  rather than the whole page at its 4xx; a request with no htmx header is
  answered as before.
- **The kernel floor is now `django-service-specs>=0.15.0`.** The request
  hooks need a permission check offered a registered seed by name, which
  0.14.0 added, and a top-level Validator field named like a seed staying the
  caller's, which 0.15.0 did.
- The request-hook checks `SpecViewBase` made are shared helpers in
  `http/utils.py`, which `SpecFormView` calls too; `SpecView` and
  `AsyncSpecView` answer exactly as before.

## [0.1.2] — 2026-10-21

### Fixed
- **The documentation and the package summary no longer claim htmx support
  the page does not have.** `SpecFormView` reads no htmx header, so an htmx
  post is answered as any other: a success is the redirect, which the request
  follows itself, and a refusal is the whole page at its 4xx status, which htmx
  does not swap by default. The PyPI summary called the page htmx-capable and
  listed htmx among its keywords, the README and the documentation site named
  htmx as a client of the JSON views, and the page's documentation and the
  view's docstring named htmx as a client that reads a refused post's status.
  They now say what the page does, and that a fragment answer for htmx is
  planned for 0.2.0. The JSON views are described as serving a script or a
  `fetch()`, since htmx swaps HTML. Two tests hold the page's answer to an htmx
  post. No behaviour changed.

## [0.1.1] — 2026-10-21

### Changed
- **The kernel floor is now `django-service-specs>=0.12.0`, the first release
  without its own copy of these views.** Until then the kernel's root still
  exported the ten JSON names as classes and functions of its own, so
  `from django_service_specs import SpecView` resolved to a second, different
  class beside this package's, and an `isinstance` check against either missed
  the other. From 0.12.0 those names are imported from `django_spec_html`
  alone.

## [0.1.0] — 2026-10-21

### Added
- **`django-spec-html`: a `django-service-specs` spec served over plain Django,
  as a page and as JSON, the kernel's two HTTP halves moved out.** The JSON
  views are the kernel's `http/` subpackage as it shipped in
  `django-service-specs` 0.11.0, and `SpecFormView` is the page the kernel last
  shipped in 0.5.1, each moved with its history, tests and documentation so
  the kernel can carry no transport. `SpecFormView` serves a spec whose
  Validator is a `FormValidator` as the page that form is: GET renders it,
  showing the row an update targets; a valid POST dispatches and redirects;
  a refused one re-renders the form with the refusal placed on it. `SpecView`,
  `AsyncSpecView`, `dispatch_request` and `adispatch_request` serve a spec as
  JSON, and `error_response`, `request_arguments`, `route_arguments`,
  `request_principal`, `add_argument_errors` and `UnsupportedMediaType` come
  with them. The behaviour is unchanged but for the one change below; the
  import paths are this package's,
  `django_spec_html` at the root, with `django_spec_html.http` and
  `django_spec_html.views` where each name is defined. `UnsupportedMediaType`
  is this package's own refusal, answered 415 by its own `error_response`,
  and the array cap keeps the kernel's `SERVICE_SPECS_MAX_ARRAY_LENGTH` key.
  Until the kernel removes its own copy of the JSON views, the kernel's root
  still exports those ten names as the kernel's own classes and functions,
  not this package's: import them from `django_spec_html`. Neither
  package needs an entry in `INSTALLED_APPS`. Floors: Python 3.10, Django 4.2
  and `django-service-specs>=0.10.0`, the first kernel release with every
  name these views read at its root.

### Changed
- **`SpecFormView` answers an `UnsupportedMediaType` an operation raises at
  415**, re-rendering the form, where the kernel's page answered it 400. The
  page and the JSON views now read one map from each refusal to its status,
  so a refused post answers at the status a JSON client reads for the same
  refusal.

[Unreleased]: https://github.com/Artui/django-spec-html/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Artui/django-spec-html/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/Artui/django-spec-html/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Artui/django-spec-html/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-spec-html/compare/v0.0.0...v0.1.0
