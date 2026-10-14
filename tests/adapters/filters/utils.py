"""FilterSets the filter tests share: real django-filter classes, never stand-ins.

The reading is checked against what django-filter itself reads, so each one is a
real ``FilterSet`` over a model the suite migrates.
"""

from __future__ import annotations

from typing import Any

import django_filters
from django.contrib.auth.models import Group, User

from tests.dispatch_app.models import Note


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """``?pk__in=1,2``: a CSV filter takes its element type from its second base."""


class NumberCsvRangeFilter(django_filters.BaseRangeFilter, django_filters.NumberFilter):
    """``?pk__range=1,5``: two values in one argument, unlike ``RangeFilter``."""


class NoteFilters(django_filters.FilterSet):
    """Every shape a note can be filtered by, with the lookup each one names."""

    title = django_filters.CharFilter()
    title_contains = django_filters.CharFilter(field_name="title", lookup_expr="icontains")
    archived = django_filters.BooleanFilter()
    min_pk = django_filters.NumberFilter(field_name="pk", lookup_expr="gte")
    pks = NumberInFilter(field_name="pk", lookup_expr="in")
    pk_between = NumberCsvRangeFilter(field_name="pk", lookup_expr="range")
    pk_range = django_filters.RangeFilter(field_name="pk")
    kind = django_filters.ChoiceFilter(
        field_name="title", choices=[("draft", "Draft"), ("final", "Final")]
    )
    kinds = django_filters.MultipleChoiceFilter(
        field_name="title", choices=[("draft", "Draft"), ("final", "Final")]
    )
    owner = django_filters.ModelChoiceFilter(queryset=User.objects.all())
    owners = django_filters.ModelMultipleChoiceFilter(
        field_name="owner", queryset=User.objects.all()
    )
    explained = django_filters.CharFilter(
        field_name="title", lookup_expr="startswith", help_text="Titles starting with this."
    )


class UserFilters(django_filters.FilterSet):
    """The date and time shapes, which a note has none of."""

    joined_on = django_filters.DateFilter(field_name="date_joined", lookup_expr="date")
    joined_since = django_filters.DateTimeFilter(field_name="date_joined", lookup_expr="gt")
    joined = django_filters.DateFromToRangeFilter(field_name="date_joined")
    joined_at = django_filters.DateTimeFromToRangeFilter(field_name="date_joined")
    joined_time = django_filters.TimeFilter(field_name="date_joined", lookup_expr="time")
    joined_between = django_filters.TimeRangeFilter(field_name="date_joined__time")
    token = django_filters.UUIDFilter(field_name="username")
    level = django_filters.ChoiceFilter(field_name="pk", choices=[(1, "One"), (2, "Two")])
    mixed = django_filters.ChoiceFilter(field_name="username", choices=[(1, "One"), ("b", "B")])
    grouped = django_filters.ChoiceFilter(
        field_name="username",
        choices=[("People", [("ada", "Ada"), ("bob", "Bob")]), ("eve", "Eve")],
    )
    deferred = django_filters.ChoiceFilter(field_name="username", choices=list)
    group = django_filters.ModelChoiceFilter(field_name="groups", queryset=Group.objects.all())
    ordering = django_filters.OrderingFilter(fields=["username"])
    spent = django_filters.DurationFilter(field_name="pk")


class NoteKeyFilters(django_filters.FilterSet):
    """Number filters on a note's integer columns, reached each way a filter can name one."""

    pk = django_filters.NumberFilter()
    owner_key = django_filters.NumberFilter(field_name="owner")
    owner_min = django_filters.NumberFilter(field_name="owner__id", lookup_expr="gte")
    pk_digits = django_filters.NumberFilter(field_name="pk", lookup_expr="contains")
    joined_in = django_filters.NumberFilter(field_name="owner__date_joined__year")
    pk_or_any = django_filters.NumberFilter(field_name="pk", method="filter_pk_or_any")

    def filter_pk_or_any(self, queryset: Any, name: str, value: Any) -> Any:
        """A key no note can have matches every note: the method's own reading."""
        return queryset if value >= 2**63 else queryset.filter(pk=value)


class UserKeyFilters(django_filters.FilterSet):
    """Filters on a user's keys reached through a many-to-many and a reverse foreign key."""

    group_key = django_filters.NumberFilter(field_name="groups")
    note_key = django_filters.NumberFilter(field_name="notes")
    note_keys = NumberInFilter(field_name="notes", lookup_expr="in")
    group = django_filters.ModelChoiceFilter(field_name="groups", queryset=Group.objects.all())
    groups = django_filters.ModelMultipleChoiceFilter(
        field_name="groups", queryset=Group.objects.all()
    )


class EditionNumberFilters(django_filters.FilterSet):
    """Number filters on a column whose type holds no negative number."""

    number = django_filters.NumberFilter()
    number_min = django_filters.NumberFilter(field_name="number", lookup_expr="gte")
    numbers = NumberInFilter(field_name="number", lookup_expr="in")
    number_range = django_filters.RangeFilter(field_name="number")


class TitleFilter(django_filters.FilterSet):
    """One filter, for the tests about applying rather than reading."""

    title = django_filters.CharFilter()


class RequestScopedFilter(django_filters.FilterSet):
    """Narrows by the request's own principal, as a view-side FilterSet does.

    ``self.request`` is what ``DjangoFilterBackend`` fills; here it is whatever
    the pool carries under ``request``, a seed an HTTP adapter registers.
    """

    mine = django_filters.BooleanFilter(method="filter_mine")

    def filter_mine(self, queryset: Any, name: str, value: Any) -> Any:
        return queryset.filter(owner=self.request.user) if value else queryset


def notes() -> Any:
    return Note.objects.all()
