"""A spec served as a Django admin action, through the page that serves it anywhere else.

Named for the concern rather than ``admin``: a module of that name is what
``admin.autodiscover()`` imports from every installed app.
"""

from django_spec_html.model_admin.spec_actions_mixin import SpecActionsMixin

__all__ = ["SpecActionsMixin"]
