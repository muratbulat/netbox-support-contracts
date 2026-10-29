from django import forms
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from extras.dashboard.utils import register_widget
from extras.dashboard.widgets import DashboardWidget, WidgetConfigForm

from .choices import CoverageStatusChoices
from .services import coverage_summary
from .tables import days_badge

PLUGIN = "netbox_support_contracts"


@register_widget
class SupportCoverageSummaryWidget(DashboardWidget):
    default_title = _("Support Coverage Summary")
    description = _("Device support coverage KPIs and the soonest-ending support contracts.")
    default_config = {"limit": 8}
    width = 6
    height = 5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.title is not None:
            self.title = str(self.title)

    def __str__(self):
        return str(super().__str__())

    class ConfigForm(WidgetConfigForm):
        limit = forms.IntegerField(
            min_value=1, max_value=50, initial=8, label=_("Contracts listed"),
            help_text=_("Number of soonest-ending contracts to show"),
        )

    def render(self, request):
        if not request.user.has_perm(f"{PLUGIN}.view_supportcontract"):
            return render_to_string(f"{PLUGIN}/widgets/coverage_summary.html", {"permitted": False})
        summary = coverage_summary(user=request.user, limit=self.config.get("limit", 8))
        report_url = reverse(f"plugins:{PLUGIN}:device_coverage")
        kpis = [
            {
                "label": label,
                "color": CoverageStatusChoices.colors[status],
                "count": summary["devices"][status],
                "url": f"{report_url}?coverage={status}",
            }
            for status, label in (
                (CoverageStatusChoices.COVERED, _("Covered")),
                (CoverageStatusChoices.EXPIRING, _("Expiring (≤{days}d)").format(days=summary["warning_days"])),
                (CoverageStatusChoices.EXPIRED, _("Expired")),
                (CoverageStatusChoices.NONE, _("Not Covered")),
            )
        ]
        contracts = [
            {"contract": contract, "days_badge": days_badge(contract.days_remaining, summary["warning_days"])}
            for contract in summary["soonest_contracts"]
        ]
        return render_to_string(f"{PLUGIN}/widgets/coverage_summary.html", {
            "permitted": True,
            "kpis": kpis,
            "contracts": contracts,
            "total": summary["devices"]["total"],
            "contract_list_url": reverse(f"plugins:{PLUGIN}:supportcontract_list"),
        })
