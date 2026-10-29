import django_tables2 as tables
from dcim.models import Device
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from netbox.context import current_request
from netbox.tables import NetBoxTable, PrimaryModelTable, columns

from .choices import ContractStateChoices, CoverageStatusChoices
from .models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from .services import OPEN_ENDED, coverage_status, days_until, get_warning_days, may_view_cost

__all__ = (
    "DeviceCoverageTable",
    "HardwareLifecycleEoLTable",
    "SupportContractTable",
    "SupportCoverageTable",
    "coverage_badge",
    "days_badge",
    "state_badge",
)


#
# Badges (shared with the device panel and the dashboard widget)
#

def _badge(label, color):
    return format_html('<span class="badge text-bg-{}">{}</span>', color, label)


def state_badge(state):
    """Green Active / yellow Expiring / red Expired."""
    return _badge(dict(ContractStateChoices)[state], ContractStateChoices.colors[state])


def coverage_badge(status):
    """Green Covered / yellow Expiring / red Expired / grey Not covered."""
    return _badge(dict(CoverageStatusChoices)[status], CoverageStatusChoices.colors[status])


def days_label(days):
    if days is None:
        return _("Open-ended")
    if days < 0:
        return ngettext("Expired %(n)d day ago", "Expired %(n)d days ago", -days) % {"n": -days}
    if days == 0:
        return _("Ends today")
    return ngettext("%(n)d day left", "%(n)d days left", days) % {"n": days}


def days_badge(days, warning_days=None):
    """Days-remaining badge; None means open-ended."""
    if days is None:
        color = "green"
    elif days <= 0:
        color = "red"
    elif days <= (get_warning_days() if warning_days is None else warning_days):
        color = "yellow"
    else:
        color = "green"
    return _badge(days_label(days), color)


#
# Columns
#

class StateColumn(tables.Column):
    """Computed lifecycle state of the row (record.state) as a colored badge."""

    empty_values = ()

    def render(self, record):
        return state_badge(record.state)

    def value(self, record):
        return record.state


class DaysRemainingColumn(tables.Column):
    """Computed days remaining (record.days_remaining) as a colored badge."""

    empty_values = ()

    def render(self, record):
        return days_badge(record.days_remaining)

    def value(self, record):
        return record.days_remaining


class EndDateColumn(columns.DateColumn):
    """End date, or "Open-ended" when blank."""

    empty_values = ()

    def render(self, value):
        return value.isoformat() if value else _("Open-ended")

    def value(self, value):
        return value.isoformat() if value else ""


#
# Tables
#

class SupportContractTable(PrimaryModelTable):
    contract_number = tables.Column(verbose_name=_("Contract Number"), linkify=True)
    manufacturer = tables.Column(verbose_name=_("Manufacturer"), linkify=True)
    tenant = tables.Column(verbose_name=_("Tenant"), linkify=True)
    state = StateColumn(verbose_name=_("State"), order_by=("end_date",))
    start_date = columns.DateColumn(verbose_name=_("Start Date"))
    end_date = EndDateColumn(verbose_name=_("End Date"))
    days_remaining = DaysRemainingColumn(verbose_name=_("Days Remaining"), order_by=("end_date",))
    device_count = columns.LinkedCountColumn(
        viewname="plugins:netbox_support_contracts:supportcoverage_list",
        url_params={"contract_id": "pk"},
        verbose_name=_("Devices"),
    )
    vm_count = columns.LinkedCountColumn(
        viewname="plugins:netbox_support_contracts:supportcoverage_list",
        url_params={"contract_id": "pk"},
        verbose_name=_("VMs"),
    )
    module_count = columns.LinkedCountColumn(
        viewname="plugins:netbox_support_contracts:supportcoverage_list",
        url_params={"contract_id": "pk"},
        verbose_name=_("Modules"),
    )
    portal_url = tables.URLColumn(verbose_name=_("Vendor Portal URL"))
    cost_display = tables.Column(verbose_name=_("Cost"), accessor="cost", order_by=("cost",))
    tags = columns.TagColumn(url_name="plugins:netbox_support_contracts:supportcontract_list")

    class Meta(PrimaryModelTable.Meta):
        model = SupportContract
        fields = (
            "pk", "id", "contract_number", "manufacturer", "service_level", "state", "start_date", "end_date",
            "days_remaining", "device_count", "vm_count", "module_count", "reseller", "cost_display", "portal_url",
            "tenant", "description", "comments", "tags", "created", "last_updated", "actions",
        )
        default_columns = (
            "pk", "contract_number", "manufacturer", "service_level", "state", "start_date", "end_date",
            "days_remaining", "device_count", "reseller", "description", "actions",
        )

    def __init__(self, *args, **kwargs):
        # The cost column is removed outright (not just hidden) so it cannot be selected, rendered, or exported.
        request = current_request.get()
        if not may_view_cost(getattr(request, "user", None)):
            kwargs["exclude"] = (*(kwargs.get("exclude") or ()), "cost_display")
        super().__init__(*args, **kwargs)

    def render_cost_display(self, record):
        return record.cost_display

    def value_cost_display(self, record):
        return record.cost_display


class SupportCoverageTable(NetBoxTable):
    """Expects a queryset annotated by services.annotate_coverage() (ordering by effective end date)."""

    contract = tables.Column(verbose_name=_("Contract"), linkify=True)
    manufacturer = tables.Column(
        verbose_name=_("Manufacturer"), accessor="contract__manufacturer", linkify=True
    )
    device = tables.Column(verbose_name=_("Device"), linkify=True)
    device_serial = tables.Column(verbose_name=_("Serial Number"), accessor="device__serial")
    device_type = tables.Column(verbose_name=_("Device Type"), accessor="device__device_type", linkify=True)
    site = tables.Column(verbose_name=_("Site"), accessor="device__site", linkify=True)
    virtual_machine = tables.Column(verbose_name=_("Virtual Machine"), linkify=True)
    module = tables.Column(verbose_name=_("Module"), linkify=True)
    sku = tables.Column(verbose_name=_("SKU"))
    start_date = columns.DateColumn(verbose_name=_("Start Date"), accessor="contract__start_date")
    effective_end_date = EndDateColumn(
        verbose_name=_("End Date"), accessor="effective_end_date", order_by=("effective_end",)
    )
    end_date = columns.DateColumn(verbose_name=_("End Date Override"))
    state = StateColumn(verbose_name=_("State"), order_by=("effective_end",))
    days_remaining = DaysRemainingColumn(verbose_name=_("Days Remaining"), order_by=("effective_end",))
    notes = tables.Column(verbose_name=_("Notes"))
    tags = columns.TagColumn(url_name="plugins:netbox_support_contracts:supportcoverage_list")

    class Meta(NetBoxTable.Meta):
        model = SupportCoverage
        fields = (
            "pk", "id", "contract", "manufacturer", "device", "device_serial", "device_type", "site",
            "virtual_machine", "module", "sku", "start_date", "effective_end_date", "end_date", "state",
            "days_remaining", "notes", "tags", "created", "last_updated", "actions",
        )
        default_columns = (
            "pk", "contract", "device", "device_serial", "device_type", "site", "virtual_machine", "sku",
            "start_date", "effective_end_date", "state", "days_remaining", "actions",
        )


class HardwareLifecycleEoLTable(PrimaryModelTable):
    device_type = tables.Column(verbose_name=_("Device Type"), linkify=True)
    module_type = tables.Column(verbose_name=_("Module Type"), linkify=True)
    manufacturer = tables.Column(verbose_name=_("Manufacturer"), linkify=True, orderable=False)
    end_of_sale = columns.DateColumn(verbose_name=_("End of Sale"))
    end_of_maintenance = columns.DateColumn(verbose_name=_("End of Maintenance"))
    end_of_support = columns.DateColumn(verbose_name=_("End of Support"))
    state = StateColumn(verbose_name=_("Support State"), order_by=("end_of_support",))
    days_remaining = DaysRemainingColumn(verbose_name=_("Days Remaining"), order_by=("end_of_support",))
    notice_url = tables.URLColumn(verbose_name=_("Notice URL"))
    tags = columns.TagColumn(url_name="plugins:netbox_support_contracts:hardwarelifecycleeol_list")

    class Meta(PrimaryModelTable.Meta):
        model = HardwareLifecycleEoL
        fields = (
            "pk", "id", "device_type", "module_type", "manufacturer", "end_of_sale", "end_of_maintenance",
            "end_of_support", "state", "days_remaining", "notice_url", "description", "comments", "tags", "created",
            "last_updated", "actions",
        )
        default_columns = (
            "pk", "device_type", "module_type", "end_of_sale", "end_of_maintenance", "end_of_support",
            "days_remaining", "notice_url", "actions",
        )


class DeviceCoverageTable(NetBoxTable):
    """Devices with their overall coverage; expects services.annotate_devices() annotations."""

    name = tables.Column(verbose_name=_("Device"), linkify=True, order_by=("name", "pk"))
    serial = tables.Column(verbose_name=_("Serial Number"))
    device_type = tables.Column(verbose_name=_("Device Type"), linkify=True)
    site = tables.Column(verbose_name=_("Site"), linkify=True)
    role = tables.Column(verbose_name=_("Role"), linkify=True)
    coverage = tables.Column(
        verbose_name=_("Coverage"),
        accessor="coverage_end_annotated",
        empty_values=(),
        order_by=("coverage_end_annotated",),
    )
    coverage_end = tables.Column(
        verbose_name=_("Coverage End"),
        accessor="coverage_end_annotated",
        empty_values=(),
        order_by=("coverage_end_annotated",),
    )
    days_remaining = tables.Column(
        verbose_name=_("Days Remaining"), accessor="coverage_end_annotated", empty_values=(),
        order_by=("coverage_end_annotated",),
    )
    contracts = columns.LinkedCountColumn(
        viewname="plugins:netbox_support_contracts:supportcontract_list",
        url_params={"device_id": "pk"},
        accessor="coverage_contracts_annotated",
        verbose_name=_("Contracts"),
    )
    actions = columns.ActionsColumn(actions=())

    class Meta(NetBoxTable.Meta):
        model = Device
        fields = (
            "pk", "id", "name", "serial", "device_type", "site", "role", "coverage", "coverage_end",
            "days_remaining", "contracts",
        )
        default_columns = (
            "name", "serial", "device_type", "site", "coverage", "coverage_end", "days_remaining", "contracts",
        )

    def render_coverage(self, value):
        return coverage_badge(coverage_status(value))

    def value_coverage(self, value):
        return coverage_status(value)

    def render_coverage_end(self, value):
        if value is None:
            return "—"
        return _("Open-ended") if value == OPEN_ENDED else value.isoformat()

    def value_coverage_end(self, value):
        if value is None:
            return ""
        return "" if value == OPEN_ENDED else value.isoformat()

    def render_days_remaining(self, value):
        if value is None:
            return "—"
        return days_badge(None if value == OPEN_ENDED else days_until(value))

    def value_days_remaining(self, value):
        return None if value in (None, OPEN_ENDED) else days_until(value)
