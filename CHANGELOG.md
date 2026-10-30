# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`validate_on_refresh` on `SpecFormView`: a post that does not commit
  shows what its commit would refuse.** `False` by default, which shows no
  error, as before. Set, such a post is read as a commit reads it, the
  server's binding laid over it, and bound through the kernel's
  `bind_arguments` with the principal admitted, the target the page found,
  the request's seeds and its call context, so a `form_kwargs` naming a seed
  is handed it. Nothing is dispatched and no `success_message` is sent; the
  refusal is placed on the form by `add_argument_errors`, at 200 with the page
  or the fragment, and `refreshed` in the context as before.
  - **`HX-Trigger-Name` decides which errors.** Where it names one of the
    form's fields, matched against its `html_name` so a form `prefix` holds,
    only that field's errors are placed and no non-field error; every other
    refresh places the whole refusal.
  - **No provider runs**, as `bind_arguments` runs none: a field the spec's
    `input_data` fills is judged as posted, so a required one left blank is
    refused by the refresh and accepted by the commit.
  - The confirmation and formset pages are unchanged.
- **The package ships its source catalog.**
  `django_spec_html/locale/en/LC_MESSAGES/django.po` lists every msgid the
  package marks, untranslated: the Python `gettext` calls and the templates'
  `{% translate %}` and `{% blocktranslate %}` text. It is what a contributed
  language starts from, and it loads for a project that lists `django_spec_html`
  in `INSTALLED_APPS`, as the shipped templates require. No translated language
  ships. `make messages` regenerates it (it needs GNU gettext) and `make
  messages-check`, which CI runs, fails when it is stale. The new
  [translations page](https://artui.github.io/django-spec-html/translations/)
  covers loading it, the kernel's catalog beside it, and merging both into a
  project's own.
  - `UnsupportedMediaType`'s default message was a bare string that
    `makemessages` never extracted. It is now marked, so it is in the catalog,
    and is still looked up in the active language each time the refusal is built
    with no message of its own.

## [0.7.0] — 2026-10-30

### Added
- **`redirect_field_name` on `SpecFormView`, `SpecFormSetView` and
  `SpecConfirmView`: a page returns where it was opened from.** `None` by
  default, which reads nothing. Named - `"next"`, as Django's `LoginView`
  names it - the URL the post or its query carries under that key is read as
  `LoginView` reads it, the post's value first, and used only where
  `url_has_allowed_host_and_scheme` allows it against the request's own host,
  with `https` required on a secure request. A success redirects there in
  place of `get_success_url(result)`, an override included, and under htmx
  answers `HX-Redirect` to it; a `spec-result` fragment still answers in
  place. An unsafe URL is ignored, and the success goes where it went before.
  `get_redirect_url()` answers the safe URL, or `""`.
  - **It survives a refused post.** The context carries
    `redirect_field_name` and `next`, the safe URL, and the shipped
    templates post them back as a hidden input; neither key is there without
    a safe URL. The key is never an argument: a form page's arguments are its
    fields and its route, and the confirmation page reads nothing posted.
  - `as_view()` refuses a name that is not a non-empty string, one that is
    the `commit_button`, on `SpecFormView` one the form declares as a field,
    and on `SpecFormSetView` one that is the `add_button` or sits under the
    formset's `form-` prefix.
- **`refusal_redirect` on `SpecConfirmView`: a refusal sent back to the page
  the button was posted from.** `False` by default. Set, an invalid, failed or
  needs-input refusal sends each message the page would show in `errors`
  through `messages.error`, with the refusal's `code` as `extra_tags` where it
  has one, and redirects to `get_refusal_url(refused)`: the safe URL
  `redirect_field_name` reads, else `success_url`. Under htmx it answers
  `HX-Redirect`. A denial is raised as before. Without the messages middleware
  Django raises `MessageFailure`, and the page is rendered as it is without
  `refusal_redirect`, so the refusal is never lost; a refusal with no message
  to send is the page again too. A refusal sent back counts and presents no
  target. `as_view()` refuses it on a
  page with no `success_url` that does not override `get_refusal_url`.
- **The `unavailable` template filter: why a row's action is unavailable.**
  `{% with why=row|unavailable:"withdraw" %}{% if why %}{{ why.reason }}{% endif %}{% endwith %}`
  returns the row's answer, with the `code` and `reason` of the first
  condition the row does not meet, when the action is unavailable, and
  `None` when it is available. It reads the answers `available` reads, so it
  costs no query, and raises the same `TemplateSyntaxError` for a name the
  spec does not declare or a row the page did not serve. A condition on the
  row asked of a row that no longer exists answers with no code and no
  reason, so `{% if why %}` holds and `{{ why.reason }}` renders empty. The
  answer is a fresh `dict` per read, so `json_script` takes it, whose `clear`
  and `popitem` are marked `alters_data`, so no template can empty it. The
  example's table shows the reason where a row has no Withdraw button.
  - **The docs read a negated or combined condition through `{% with %}`**,
    for both filters, where they said to use `available` only as a bare
    `{% if %}` condition: Django's `{% if %}` reads an error inside `not`,
    `and`, `or` or a comparison as the condition being false, so a misspelt
    name there draws nothing and raises nothing, and
    `{% with ok=row|available:"withdraw" %}{% if not ok %}` resolves the filter
    outside `{% if %}`, where a misspelt name raises.
- **`SpecSelectorView.get_query(request)`, the query string a page reads.**
  It answers `request.GET` unless a subclass answers another `QueryDict`, and
  the page asks it once a request. On a list page the paging keys, the filter
  form, the spec's arguments and the previous and next links all read its
  answer; an instance page, which is not paged, reads the spec's arguments
  from it. The template is handed a `request` whose `GET` is that answer, so a
  django-tables2 table's header sort links keep it. A list that opens where
  the last visit left it answers a query kept in the session on a bare visit;
  the selector guide shows one, with a `?reset=1` that forgets a saved query
  the spec refuses. Like every request hook it is a plain `def`, and
  `as_view()` refuses an `async def` one; an answer that is not a `QueryDict`
  raises `TypeError` naming the hook.

### Changed
- **The htmx handler example answers an anonymous visitor with the login page
  whether or not htmx asked, and gains a `handler404`.** Without htmx, a
  visitor who is not signed in is redirected to the login page and back to the
  page they asked for, where the 403 page used to be shown; a signed-in
  visitor still gets Django's 403 page. The new `handler404` answers an htmx
  request with `HX-Refresh`, since htmx swaps no 4xx body and a stale row's
  action would otherwise do nothing visible, and a browser with Django's 404
  page. The pages state that the same handlers answer a list row's and a
  confirmation's denial, and that the sync pages serve under ASGI, with the
  reason there is no async page.
- **`SpecSelectorView` ignores a query key its spec does not declare.**
  `unknown_arguments` defaults to `UnknownArguments.IGNORE` on the list and
  instance pages, so `?utm_source=x`, or a bookmark from before a filter was
  renamed, is answered 200 with the rows the rest of the query selects, where
  it was a 400 with `utm_source: Unknown argument.` A declared key's value is
  read and refused as before, a route kwarg the spec does not declare is
  still the host's misconfiguration, and the JSON views still refuse an
  undeclared key. The form, formset and confirmation pages keep `REJECT` for
  what they dispatch, their form's fields and the route. Set
  `unknown_arguments = UnknownArguments.REJECT` on a list whose query only
  code builds to keep the refusal. A list page and a JSON list now read each
  value of one query string the same way, rather than answering it the same.

## [0.6.0] — 2026-10-29

### Added
- **`SpecSelectorView`: a selector spec served as a page to read.** It
  answers `GET` and `HEAD`, admits the request as every page does, reads the
  query string as the spec's arguments and runs `dispatch`. A `LIST` spec is a
  list page, a `RETRIEVE` spec one row's page, and a row the selector does not
  find is `Http404`. The template is handed the selector's own rows, as
  `rows` and, on an instance page, `row`; the shipped `spec_selector.html`
  shows each as its Output marks it, so `as_view()` refuses a spec whose
  Output it cannot display unless `table_class` is set or
  `display_rows = False`.
  - **Paging** by the kernel's `paginate_output`, one `COUNT` and the page:
    `page_kwarg`, `paginate_by`, `max_page_size`, and an opt-in
    `page_size_kwarg` a client chooses the size with, bounded by
    `max_page_size`. The paging keys are taken off the query before the spec
    reads it, so `?page=2` is never refused as an undeclared argument, and
    `as_view()` refuses a list spec declaring a parameter under a paging key.
    A page or size that is not a whole number is the default; the context's
    `page`, `previous_page_query` and `next_page_query` keep every filter.
  - **Filters**: the spec's `filter_set`, as `filter`, bound to the query for
    display and never validated by the page. Dispatch's refusal is placed on
    its form, and the page renders at the refusal's status with every
    message in `errors`; an htmx request is answered with the results alone,
    a refusal at 200.
  - **django-tables2, composed and never imported.** `table_class` builds a
    host's `Table` over the page's rows with no request and `order_by=()`,
    so the table never pages or reorders what the spec returned.
    `table_order_by_field` names the spec's own ordering parameter for the
    header links, and `as_view()` refuses one the spec does not declare.
- **The `available` template filter**, `{% load django_spec_html %}` and
  `{% if row|available:"withdraw" %}`. It reads the answers the page
  attached to each row it served, from the flags dispatch annotated on the
  page's one query, so asking costs no query and a page costs the same
  number of queries for three rows as for thirty. A name the row's spec does
  not declare raises `TemplateSyntaxError`, naming those it does.
- `docs/selectors.md` and a runnable example, `docs/examples/spec_selector.py`:
  a list page, a table page with a column asking each row what it offers, an
  instance page and the confirmation page a row links to.

### Changed
- **A query string and a form are read by the kernel's `flat_arguments`.**
  `request_arguments` reads one through the kernel's reader rather than this
  package's own copy of it, with the same rules: the comma split for typed
  arrays, a blank as absent unless `""` is a value the parameter takes, an
  array read no further than one past its cap, and the CSRF field dropped.
- **Requires `django-service-specs>=0.20.0`**, the release carrying that
  reader; the list pages need 0.19.0's `row_affordance_answers`.

### Fixed
- **A changelist action's selection is read by the same flat rules.** The
  admin page read its selected keys with `getlist` alone, so `?pks=1,2` was
  one key the parameter refused rather than two, and a blank selection was a
  malformed key rather than none.

## [0.5.0] — 2026-10-27

### Added
- **The admin face: a spec's page served as a `ModelAdmin`'s action.**
  `SpecActionsMixin` reads a `spec_actions` tuple of `SpecAction` entries and
  serves each through the pages this package already ships: `SpecFormView`
  for a spec with a `FormValidator`, `SpecConfirmView` for one without, each
  at an admin URL of its own from `get_urls()`, wrapped in
  `admin_site.admin_view()` and named `admin:<app>_<model>_<name>`.
  - A spec with an `instance_selector_spec` is an **object action**, linked
    from the change form's object tools; the change URL's object id fills the
    parameter the entry names.
  - A spec with a `collection_selector_spec` is a **changelist action**,
    offered in the action dropdown; it redirects to its page with the selected
    keys in the query string, bound as a server argument so a posted value
    cannot replace them. The selector still decides the set: a ticked row it
    does not return is not acted on.
  - **The admin's own scope comes first.** Each page needs the admin's view
    or change permission, as the changelist and the change form do, and reads
    only rows the `ModelAdmin`'s `get_queryset` returns: a changelist action's
    keys are narrowed to them before the selector sees them, and an object
    action on a row outside them is a 404. A key edited into the query string
    or the URL reaches nothing the admin would not show.
  - **An action is offered only to a principal the spec's class-level check
    admits**, the link and the dropdown entry alike, and the page's own checks
    remain the gate on its post. A staff member the spec refuses who reaches
    the URL directly gets the page's denial.
  - The mixin sets `change_form_template` to
    `django_spec_html/admin/change_form.html`, which adds the object-tools
    links. A `ModelAdmin` with a change form template of its own extends that
    one to keep them.
  - Admin templates under `django_spec_html/admin/`: the admin's chrome,
    breadcrumbs and messages around the same `spec-form` fragment, so an htmx
    request is answered as on the plain pages. A success redirects to the
    change page or the changelist with the page's `success_message`.
  - **Refused when the admin is built**, naming the admin and the entry: a
    label that does not format with the model's names (write `%%` for a
    literal percent sign), a `many` spec or one with no target, a name that is not a slug, repeats
    another or is one of the admin's own URL names, a parameter the selector
    does not read (or, for a changelist action, does not read as an array, or
    reads with a `min_length` an empty selection fails), and a page keyword
    the mixin sets itself.
  - The confirmation an admin page asks gates the form route only; the agent
    route beside it, where the admin carries an agent sidebar, is gated by the
    agent runtime. The new docs page, "In the admin", says so.
- `SpecAction` and `SpecActionsMixin` are importable from the package root,
  and `import django_spec_html` still works in a project without
  `django.contrib.admin` installed.

## [0.4.0] — 2026-10-27

### Added
- **A spec callable naming `request` or `view` is handed this request and the
  view serving it**, on both halves. `dispatch_request` and
  `adispatch_request` dispatch with a call context holding `request` and
  `view`, and the pages start their one call context per request with
  `request` and the page itself. The kernel hands each to any stage that
  names it - a seed's resolver, a selector, a permission check, an affordance
  condition, a `FormValidator`'s `form_kwargs`, the service - so a callable
  written for HTTP binds here rather than receiving `None`. The pages also
  resolve their own pool, the one their affordances and target display read,
  against that context, so a `get_pool_seeds` resolver naming `request`
  binds on a page's GET and on a refused post. No `request` seed is
  registered: `SpecView.pool_seeds.reserved` is unchanged.
- **`view=` on `dispatch_request` and `adispatch_request`**, keyword-only and
  `None` by default, for a host's own view to pass itself. `SpecView` and
  `AsyncSpecView` pass `view=self`.

### Changed
- **The kernel refuses a spec that declares `request` or `view` itself**, now
  that the two are the call's: a parameter or a `kwargs` provider key of
  either name raises `ImproperlyConfigured` on the first request, a page's GET
  included; `as_view()` does not refuse it.
  A Validator field of either name is not refused; it validates into `data`,
  and a service naming `request` or `view` beside it is handed the call's
  object, where this package used to hand it the submitted value. Read the
  field from `data`.
- The `django-service-specs` floor is `>=0.18.0`, the release that reads
  `request` and `view` off the call context for every callable it binds.

## [0.3.0] — 2026-10-26

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
  spoken as their displays, and the field marked `LABEL` names the row. A row
  carries its `fields` and the same fields as `pairs`, the one a template loops
  over, since `fields.items` reads a field named `items`. A set's
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

[Unreleased]: https://github.com/Artui/django-spec-html/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/Artui/django-spec-html/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Artui/django-spec-html/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Artui/django-spec-html/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/django-spec-html/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Artui/django-spec-html/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Artui/django-spec-html/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/Artui/django-spec-html/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Artui/django-spec-html/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-spec-html/compare/v0.0.0...v0.1.0
