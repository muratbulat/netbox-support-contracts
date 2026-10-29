"""NetBox Support Contracts: support contract, warranty coverage, and hardware EoL tracking."""

from django.utils.translation import gettext_lazy as _
from netbox.plugins import PluginConfig

__version__ = "1.0.0"


class NetBoxSupportContractsConfig(PluginConfig):
    name = "netbox_support_contracts"
    verbose_name = _("NetBox Support Contracts")
    description = _("Enterprise support contract, warranty coverage, and hardware EoL tracking for NetBox")
    version = __version__
    author = "muratbulat"
    base_url = "support-contracts"
    min_version = "4.7.0"
    max_version = None
    required_settings = []
    # Explicit path: the default ("graphql.schema") expects a graphql.py module rather than a package.
    graphql_schema = "graphql.schema.schema"
    default_settings = {
        # Contracts/coverage ending within this many days are reported as "expiring".
        "warning_days": 90,
        # True: own top-level navigation menu. False: items under the shared "Plugins" menu.
        "top_level_menu": True,
    }

    def ready(self):
        super().ready()
        # Registers the dashboard widget, the daily expiry job, and its event types.
        from . import jobs, widgets  # noqa: F401


config = NetBoxSupportContractsConfig
