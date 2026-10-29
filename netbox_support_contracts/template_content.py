from django.core.exceptions import ObjectDoesNotExist
from netbox.plugins import PluginTemplateExtension

from .services import get_warning_days, object_coverage
from .tables import coverage_badge, days_badge, state_badge

PLUGIN = "netbox_support_contracts"


class CoveragePanel(PluginTemplateExtension):
    """"Support & Warranty Coverage" card on device and virtual machine pages."""

    models = ["dcim.device", "virtualization.virtualmachine"]

    def right_page(self):
        user = self.context["request"].user
        if not user.has_perm(f"{PLUGIN}.view_supportcoverage"):
            return ""
        obj = self.context["object"]
        coverage = object_coverage(obj, user=user)
        rows = [
            {
                "coverage": row,
                "contract": row.contract,
                "state_badge": state_badge(row.state),
                "days_badge": days_badge(row.days_remaining),
            }
            for row in coverage["rows"]
        ]
        param = "device" if obj._meta.model_name == "device" else "virtual_machine"
        return self.render(f"{PLUGIN}/inc/device_coverage_panel.html", extra_context={
            "coverage": coverage,
            "coverage_rows": rows,
            "status_badge": coverage_badge(coverage["status"]),
            "summary_days_badge": (
                days_badge(None) if coverage["open_ended"]
                else days_badge(coverage["days_remaining"]) if coverage["end_date"] else ""
            ),
            "assign_param": param,
            "warning_days": get_warning_days(),
            "can_add": user.has_perm(f"{PLUGIN}.add_supportcoverage"),
        })


class LifecyclePanel(PluginTemplateExtension):
    """Vendor lifecycle (EoS / EoL) card on device type and module type pages."""

    models = ["dcim.devicetype", "dcim.moduletype"]

    def right_page(self):
        user = self.context["request"].user
        if not user.has_perm(f"{PLUGIN}.view_hardwarelifecycleeol"):
            return ""
        obj = self.context["object"]
        try:
            eol = obj.support_eol
        except ObjectDoesNotExist:
            eol = None
        return self.render(f"{PLUGIN}/inc/lifecycle_panel.html", extra_context={
            "eol": eol,
            "type_param": obj._meta.model_name.replace("type", "_type"),
            "state_badge": state_badge(eol.state) if eol and eol.end_of_support else "",
            "days_badge": days_badge(eol.days_remaining) if eol and eol.end_of_support else "",
            "can_add": user.has_perm(f"{PLUGIN}.add_hardwarelifecycleeol"),
        })


template_extensions = [CoveragePanel, LifecyclePanel]
