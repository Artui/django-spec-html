# Serving a read

A selector spec served as a page: a list of rows a person filters and pages
through, or one row. `SpecSelectorView` admits the request as every page
does, reads the query string as the spec's arguments, runs the kernel's
`dispatch`, and renders the rows the selector returned. It answers `GET` and
`HEAD` and nothing else, since a read changes nothing; an operation a row
offers is a link to the page that runs it, such as a
[`SpecConfirmView`](confirm.md).

```python
from django_spec_html import SpecSelectorView
```

The example lists books, filters them by title and author, and offers to
withdraw a published one. Its specs:

```python
--8<--
docs/examples/spec_selector.py:spec
--8<--
```

and its URLconf, a list page, the same list as a table, a page for one book
and the page that withdraws it:

```python
--8<--
docs/examples/spec_selector.py:urls
--8<--
```

The module is a whole project's URLconf over the shipped templates, so it
serves real pages: set `ROOT_URLCONF = "docs.examples.spec_selector"`, list
`django_spec_html`, `django_tables2` and `tests.adapter_app` in
`INSTALLED_APPS`, migrate, call `docs.examples.spec_selector.seed()` once from
a shell, and open `/books/`.

## What the page renders

`as_view()` refuses a spec that is not a `SelectorSpec`, or a source that
does not compile to one, when the URLconf is imported, and points a write at
the form pages. The context holds `view` and `spec`, as every page's does, and:

| Key | On a list | On an instance page |
| --- | --- | --- |
| `rows` | the page's rows | the one row, or none where `allow_none` let a missing one through |
| `row` | - | the one row |
| `page` | the kernel's `OutputPage`: `page`, `limit`, `total`, `total_pages`, `has_next`, and `items`, which is `rows` | - |
| `previous_page_query`, `next_page_query` | the query string of each neighbouring page, where it exists | - |
| `filter` | the spec's `filter_set`, bound to the query string | the same |
| `row_displays` | each row with its `label` or `pairs`, as its Output marks it | the same |
| `table` | the `table_class` table, where one is set | the same |
| `errors`, `code` | on a refusal, in place of the rows | the same |

A key with nothing to hold is absent, so `{% if page %}` is false on an
instance page. A row the selector does not find is `Http404`, as the form
pages answer it.

**The rows are the selector's own**, never a presenter's copy: a template
reads `row.author.name` off a `Book` it was handed. What a person is shown by
default is each row as the spec's Output marks it - its `label`, or its
fields with the handle left out - which is `row_displays`, presented with the
context dispatch built for the call. So `as_view()` refuses a spec whose
Output the page cannot display, unless the page is told it needs no
display: `table_class` set, or `display_rows = False` for a template of the
project's own.

## Paging

A list is paged by the kernel's `paginate_output`: one `COUNT`, then the page
of rows, both clamped, so page 99 of three is page three and its links lead
where their labels say.

| Attribute | Default | |
| --- | --- | --- |
| `page_kwarg` | `"page"` | the query key naming the page |
| `paginate_by` | `None`, the kernel's 100 | the page size |
| `max_page_size` | `None` | the ceiling on any page size |
| `page_size_kwarg` | `None` | a query key a client chooses the page size with, up to `max_page_size`, which it then needs |

**The paging keys never reach the spec.** They are taken off the query before
the spec reads it, so `?page=2` is not refused as an argument the spec does
not declare, and `as_view()` refuses a list spec declaring a parameter under
a paging key, since that parameter could never be sent. Rename the key with
`page_kwarg` instead. A page that is not a whole number is page one, and a
size that is not one is `paginate_by`. An instance page is not paged, so its
query is all the spec's: there `?page=2` is an argument like any other.

The links keep every other key, the filters and a client's page size among
them, and replace the page.

## Filters

A spec's `filter_set` is the page's filter form, as `filter`: bound to the
query string for display, with the paging keys taken off. **The page never
validates it.** Dispatch reads the same query through the same filter set and
is the one reader that decides; its refusal is placed on that form by
[`add_argument_errors`][django_spec_html.http.add_argument_errors.add_argument_errors],
so `filter.form.errors` is the refusal, and an error the form would have
raised in words of its own is not shown twice. A refused query renders the
page at its refusal's status - 400 for a value the reads refuse - with the
messages in `errors` and no rows.

The shipped page draws the form's fields with no errors beside them, and
every message of a refusal in the results, so a page that swaps its results
under htmx shows them too.

## What a row offers

A selector spec's `affordances` name the operations a row offers, and dispatch
answers every condition for every row in the page's one query: a `Q` as an
annotation on it, a callable once. The page attaches those answers to each
row it serves, and the `available` filter reads them:

```django
{% load django_spec_html %}
{% for row in rows %}
  {{ row.title }}
  {% if row|available:"withdraw" %}<a href="{% url "withdraw-book" row.pk %}">Withdraw</a>
  {% else %}<span>Not available</span>{% endif %}
{% endfor %}
```

So the button follows the rule the operation's own dispatch enforces, and
asking costs no query. **A name the spec does not declare raises**
`TemplateSyntaxError`, naming those it does, rather than reading as
unavailable for good. Use the filter as an `{% if %}` condition on its own,
with the unavailable branch as the `{% else %}`: Django reads an error
raised inside `not`, `and`, `or` or a comparison as the whole condition
false, so `{% if not row|available:"withdarw" %}` draws nothing and raises
nothing.

A row that is a mapping, such as one from `values()`, is answered on a copy,
so a selector's own rows are left as they were. A row that cannot carry an
attribute, such as a tuple from `values_list()`, is served without answers,
and the filter raises for it.

## A django-tables2 table

`table_class` names a [django-tables2](https://django-tables2.readthedocs.io/)
`Table`. The page builds it over the rows it served, as `table`, and the
shipped template includes the table's own template. **The package does not
depend on django-tables2**: install it and list `django_tables2` in
`INSTALLED_APPS` in a project that sets `table_class`.

```python
--8<--
docs/examples/spec_selector.py:table
--8<--
```

A `TemplateColumn` asks each `record` what it offers, as a template asks a
row. **The table never pages or orders the rows**: they are the spec's page,
in the spec's order. It is built with no `request`, since django-tables2 then
runs its `RequestConfig`, which would page and order the data from the query
string, and with `order_by=()`, so a `Meta.order_by` is never applied.

With no `table_order_by_field`, no column is orderable and no header links a
sort. Set it to a parameter the spec declares - a filter set's
`OrderingFilter`, `o` in the example - and each orderable column's header
links `?o=<column>`, which dispatch reads as any other argument. `as_view()`
refuses a field the spec does not declare. Mark a column the filter cannot
order by `orderable=False`, as the example's `author` is: its link would send
a value the `OrderingFilter` refuses, and answer 400.

Two limits follow from the table never being told the order. A header always
links the ascending sort, so a second click does not reverse it; a link
reversing it is the template's to write, from `filter.form`. And a header link
keeps the page the person was on, where a new order usually starts again from
page one.

The shipped page includes the table's template rather than calling
`{% render_table %}`, which the package does not load, so a `TemplateColumn`
renders without the page's context, and a `{% csrf_token %}` in one renders
nothing. A form posting from a column belongs on a template of the project's
own that renders the table with `{% render_table table %}`.

## An htmx request

An htmx request is answered with the results alone, `spec_selector_fragment.html`
by default or the template's `spec-results` partial, as the form pages answer
with their form ([an htmx request](pages.md#an-htmx-request)). A refusal is
the fragment at 200, since htmx swaps no 4xx by default, and every response
carries `Vary: HX-Request`. The shipped page wraps its results in
`<div id="spec-results">` and adds no `hx-` attribute of its own; a live
search is the project's:

```django
<form method="get" hx-get="" hx-target="#spec-results" hx-trigger="input changed delay:300ms">
  {{ filter.form }}
</form>
```

## How many queries a page costs

A list page costs the same number of queries whatever its size: the
`COUNT`, the page of rows with every row's affordance flags annotated on it,
and whatever the template asks of its own - in the example, the author
filter's choices. The example's tests hold three queries for both its pages
at three books and at thirty. A query per row is the template's, from a
relation the selector does not join, which is why the example's `books()`
selects the author the table shows.

## Server arguments, seeds and route values

As on every page: a route's captured values are arguments, `pool_seeds` and
`get_pool_seeds` add to the call's seeds, and `server_arguments` fills a
parameter from the server's side, outranking a value the query string sends
([the request](pages.md)).
