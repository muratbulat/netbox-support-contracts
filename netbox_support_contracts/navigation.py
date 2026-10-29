from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem, get_plugin_config

PLUGIN = "netbox_support_contracts"


def _item(model, label, add=True, import_=True):
    buttons = []
    if add:
        buttons.append(PluginMenuButton(
            link=f"plugins:{PLUGIN}:{model}_add",
            title=_("Add"),
            icon_class="mdi mdi-plus-thick",
            permissions=[f"{PLUGIN}.add_{model}"],
        ))
    if import_:
        buttons.append(PluginMenuButton(
            link=f"plugins:{PLUGIN}:{model}_bulk_import",
            title=_("Import"),
            icon_class="mdi mdi-upload",
            permissions=[f"{PLUGIN}.add_{model}"],
        ))
    return PluginMenuItem(
        link=f"plugins:{PLUGIN}:{model}_list",
        link_text=label,
        permissions=[f"{PLUGIN}.view_{model}"],
        buttons=tuple(buttons),
    )


contract_items = (
    _item("supportcontract", _("Support Contracts")),
    _item("supportcoverage", _("Coverage")),
    PluginMenuItem(
        link=f"plugins:{PLUGIN}:device_coverage",
        link_text=_("Device Coverage"),
        permissions=["dcim.view_device", f"{PLUGIN}.view_supportcoverage"],
    ),
)
partner_items = (
    _item("integrator", _("Integrator Companies")),
    _item("maintenancecompany", _("Maintenance Companies")),
)
lifecycle_items = (_item("hardwarelifecycleeol", _("Hardware EoL")),)

if get_plugin_config(PLUGIN, "top_level_menu"):
    menu = PluginMenu(
        label=_("Support Contracts"),
        icon_class="mdi mdi-file-certificate-outline",
        groups=(
            (_("Contracts"), contract_items),
            (_("Partners & Providers"), partner_items),
            (pgettext_lazy("support contracts", "Lifecycle"), lifecycle_items),
        ),
    )
else:
    menu_items = (*contract_items, *partner_items, *lifecycle_items)
