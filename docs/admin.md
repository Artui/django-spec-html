# In the admin

A spec's page served as a Django admin action: the same declaration, the
same page and the same checks, inside the admin's chrome. `SpecActionsMixin`
is a `ModelAdmin` that offers each spec it declares as an action, and serves
it with the page that serves it anywhere else, `SpecFormView` for a spec with
a `FormValidator` and `SpecConfirmView` for one with none. The admin
supplies the templates and the styling; nothing about the operation is
restated for it.

```python
from django_spec_html import SpecAction, SpecActionsMixin
```

This is not a framework for admin actions. `django-object-actions` and
`django-admin-confirm` are maintained and better at being one. The admin face
exists so that one declaration reaches the admin as well, with its own
permission checks, its target and its confirmation intact.

## Two kinds of action

**Which kind is the spec's target's answer**, never the declaration's:

| The spec's target | The action | Where it is offered |
| --- | --- | --- |
| an `instance_selector_spec` | an object action, on one row | a link among the change page's object tools |
| a `collection_selector_spec` | a changelist action, on the selected rows | an entry in the changelist's action dropdown |

A spec with neither, a create, and a `many` spec, whose page is
[`SpecFormSetView`](formsets.md)'s, are not admin actions: the mixin refuses
them when the admin is registered.

The example declares one of each. Withdrawing a book acts on the row, and has
nothing to fill in; repricing acts on the selected books, and asks for the
price:

```python
--8<--
docs/examples/admin.py:specs
--8<--
```

Each is a `SpecAction` on the admin's `spec_actions`: a name, the spec, the
label the admin shows, and the `parameter` of the target selector the admin
fills. Register the admin as any other:

```python
--8<--
docs/examples/admin.py:admin
--8<--
```

## The URL each action is served at

`get_urls()` adds one URL per action, ahead of the admin's own and each
wrapped in `admin_site.admin_view()`, so only a signed-in staff member the
site admits reaches it, and anyone else is sent to the admin's sign-in page:

| The action | Its URL | Its name |
| --- | --- | --- |
| object | `<object_id>/actions/<name>/` | `admin:<app>_<model>_<name>` |
| changelist | `actions/<name>/` | `admin:<app>_<model>_<name>` |

**An object action** fills its `parameter` with the change page's
`object_id`, through the page's `route_kwargs`, unquoted as the admin quotes
a key into its URLs.

**A changelist action** is an entry in the dropdown that redirects to its
page with the selected rows' primary keys in the query string, repeated under
its `parameter` (`?pks=3&pks=5`), which must be an `array` the collection
selector reads. The page binds them to that parameter as `server_arguments`,
read from the query string on GET and on the post the page sends back to its
own URL. So **nothing posted stands in for the selection**: a value posted
under the name is dropped, as every value a binding answers for is. The
keys are first narrowed to the admin's `get_queryset()`, as the changelist
narrows the rows its own actions run on, so a key outside it never reaches
the selector ([the admin's scope](#the-admins-scope-comes-first)). Within
that, **the selector still decides the set**: a key it does not answer is
not acted on, so the example reprices the published books among those
ticked and leaves a draft as it was. A key the parameter or the primary key
refuses, `?pks=abc`, is a 400.

The keys travel in the URL, so a selection is as long as a URL and as the
parameter takes: an array past its `max_length`, or past the kernel's
`SERVICE_SPECS_MAX_ARRAY_LENGTH` where it declares none, is refused at 400.
Selecting every row of a long changelist is a filter's job, declared on the
selector, rather than a list of keys.

## The admin's scope comes first

A page is the admin's as much as its change page is, so it answers to the
admin's own gates before the spec's, as Django's actions and change page do:

- **The admin's permission.** A page is served only to a principal the
  `ModelAdmin`'s `has_view_or_change_permission()` admits, asked of the row
  on an object action; anyone else is refused with a 403.
- **The admin's rows.** An object action's row is the admin's
  `get_object()`, read from its `get_queryset()`, so a row outside it is a
  404, as the change page answers it. A changelist action's selection is
  narrowed to the same queryset before it is bound.

**The spec's own checks run inside that scope**, on every request, the
object-level check included, and its selector decides the set within it. So
an admin whose `get_queryset()` shows each staff member their own rows keeps
every other row from its spec actions too, whatever the spec's checks or
selector would admit, and neither the URL nor the query string reaches past
it.

## Who is offered an action

**Only a principal the spec and the admin both admit.** The object-tools link
and the dropdown entry are shown where the kernel's `authorize`, the spec's
class-level check, admits the signed-in principal, asked with the page's own
seeds and call context, and the admin's `has_view_or_change_permission()`
does too. The admin's model permissions are not the whole gate: Django
offers an action that declares no `allowed_permissions` to anyone who can
view the changelist, and a spec's checks say what its operation needs
whatever the admin shows. So the example's `IsEditor` decides as well, and a
staff member who may view the books and is not an editor sees neither the
link nor the entry.

The offer is advice. **The page's own checks are the gate**: a principal who
reaches the URL directly is admitted or refused by the admin's scope and the
spec, as on any route, and a refusal is the admin's 403. An entry not offered
is not among `get_actions()`, which the changelist reads again on the post
that runs an action, so posting its name runs nothing.

## The page

The page is the spec's page, with every attribute it takes. `page_kwargs`
hands it any keyword its `as_view()` takes - `display_target`,
`commit_button`, `asked_fields`, `success_message`, `pool_seeds` - and
`as_view()` checks them when the admin is registered, as it checks any page.
The mixin answers for the rest, and refuses them in `page_kwargs`: the spec,
the templates, `success_url`, `route_kwargs`, and `server_arguments` with
`get_server_arguments`, since the binding is a changelist action's selection;
a value the server answers for comes from a pool seed instead.

Its templates are `django_spec_html/admin/spec_form.html` and
`django_spec_html/admin/spec_confirm.html`. Each extends
`admin/base_site.html` - the admin's header, its stylesheets, the form's
media, the action's label as the title and breadcrumbs back through the
changelist and the row - around **the very fragment the shipped page
includes**, so an htmx request is answered with that fragment, as it is
anywhere else. The context is the page's, with the admin site's own
`each_context()`, `opts`, `title`, `spec_action`, `object_id` on an object
action and `has_view_permission` added. Override either template the Django
way, in a directory of the project's own.

**A success returns to the admin**: an object action to the row's change
page, a changelist action to the changelist, with the page's
`success_message` among the admin's messages. A refusal is the admin page
again with the refusal on its form, at the status any page answers it with,
and a question a service asks with `AdditionalInputRequired` is asked there
too.

The change page is rendered from `django_spec_html/admin/change_form.html`,
which extends `admin/change_form.html` and lists the object actions ahead of
the admin's own tools. A `ModelAdmin` with a `change_form_template` of its
own extends that template to keep the links.

## The confirmation gates the form route only

The page's confirmation, a [question](confirm.md) a service asks and a person
answers, is the admin page's own, and gates the post a person sends from it.
**It is no gate on an agent route.** The spec is one declaration for every
route, and where the admin also offers an agent - a sidebar any page
extending the admin's base carries, this one included - the same operation is
reachable a second time, through the agent, on the same page. There the
`AdditionalInputRequired` goes back to the model, which can answer it itself,
as [Confirming an operation](confirm.md#not-a-gate-on-an-agent-route) says.
The human gate on that route is the agent runtime's:
[django-spec-agents](https://artui.github.io/django-spec-agents/)' `ToolGuard`,
whose approval card a person answers before the tool runs.

## What is refused when the admin is registered

The pages are built when the `ModelAdmin` is, at `admin.site.register` or
`@admin.register`, so a declaration the admin could not serve fails the
project's start rather than its first request. `ImproperlyConfigured`
names the admin and the action, for:

- an entry that is not a `SpecAction`;
- a name that is not a slug, is used twice, or is one of the admin's own URL
  names for the model (`add`, `change`, `changelist`, `delete`, `history`);
- a label that does not format with the model's names, as Django formats an
  action's description for the dropdown and as the link and the page's title
  are formatted too: `%(verbose_name)s` and `%(verbose_name_plural)s` are
  the names, and a literal `%` is written `%%`;
- a spec that is not a `ServiceSpec`, a `many` spec, or one with no target;
- a `parameter` the target selector does not read, or, on a changelist
  action, one it does not read as an `array`;
- on a changelist action, a `parameter` declaring a `min_length` of one or
  more: the page is declared with the selection bound to no row, since which
  rows are selected is known only on a request, and the kernel refuses a
  binding its parameter does not take. Selecting no row is then a page acting
  on none, rather than a refusal;
- a `page_kwargs` key the mixin answers for;
- anything the page's own `as_view()` refuses, under the admin's name for the
  action, `BookAdmin['withdraw']`, so the message says which declaration it
  is about.
