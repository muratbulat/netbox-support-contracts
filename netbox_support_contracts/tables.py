import django_tables2 as tables
from dcim.models import Device
from django.core.exceptions import ObjectDoesNotExist
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from netbox.context import current_request
from netbox.tables import NetBoxTable, PrimaryModelTable, columns

from .choices import ContractStateChoices, CoverageStatusChoices
from .models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from .services import (
    OPEN_ENDED,
    contract_state,
    coverage_status,
    days_label,
    days_until,
    get_warning_days,
    may_view_cost,
)

__all__ = (
    "DeviceCoverageTable",
    "HardwareLifecycleEoLTable",
    "SupportContractTable",
    "SupportCoverageTable",
    "coverage_badge",
    "days_badge",
    "register_core_columns",
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
    contract_type = columns.ChoiceFieldColumn(verbose_name=_("Type"))
    manufacturer = tables.Column(verbose_name=_("Manufacturer"), linkify=True)
    tenant = tables.Column(verbose_name=_("Tenant"), linkify=True)
    state = StateColumn(verbose_name=pgettext_lazy("support contracts", "State"), order_by=("end_date",))
    start_date = columns.DateColumn(verbose_name=_("Start Date"))
    end_date = EndDateColumn(verbose_name=_("End Date"))
    days_remaining = DaysRemainingColumn(verbose_name=_("Days Remaining"), order_by=("end_date",))
    device_count = columns.LinkedCountColumn(
        viewname="plugins:netbox_support_contracts:supportcoverage_list",
        url_params={"contract_id": "pk"},
        verbose_name=pgettext_lazy("support contracts", "Devices"),
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
    sku_count = columns.LinkedCountColumn(
        viewname="plugins:netbox_support_contracts:supportcoverage_list",
        url_params={"contract_id": "pk"},
        verbose_name=_("Licenses / SKUs"),
    )
    portal_url = tables.URLColumn(verbose_name=_("Vendor Portal URL"))
    cost_display = tables.Column(verbose_name=_("Cost"), accessor="cost", order_by=("cost",))
    tags = columns.TagColumn(url_name="plugins:netbox_support_contracts:supportcontract_list")

    class Meta(PrimaryModelTable.Meta):
        model = SupportContract
        fields = (
            "pk", "id", "contract_number", "contract_type", "manufacturer", "service_level", "state", "start_date",
            "end_date", "days_remaining", "device_count", "vm_count", "module_count", "sku_count", "reseller",
            "cost_display", "portal_url", "tenant", "description", "comments", "tags", "created", "last_updated",
            "actions",
        )
        default_columns = (
            "pk", "contract_number", "contract_type", "manufacturer", "service_level", "state", "start_date",
            "end_date", "days_remaining", "device_count", "reseller", "description", "actions",
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
    contract_type = columns.ChoiceFieldColumn(verbose_name=_("Contract Type"), accessor="contract__contract_type")
    reseller = tables.Column(verbose_name=_("Reseller"), accessor="contract__reseller")
    sku = tables.Column(verbose_name=_("SKU"))
    quantity = tables.Column(verbose_name=_("Quantity"))
    start_date = columns.DateColumn(verbose_name=_("Start Date"), accessor="contract__start_date")
    effective_end_date = EndDateColumn(
        verbose_name=_("End Date"), accessor="effective_end_date", order_by=("effective_end",)
    )
    end_date = columns.DateColumn(verbose_name=_("End Date Override"))
    state = StateColumn(verbose_name=pgettext_lazy("support contracts", "State"), order_by=("effective_end",))
    days_remaining = DaysRemainingColumn(verbose_name=_("Days Remaining"), order_by=("effective_end",))
    notes = tables.Column(verbose_name=_("Notes"))
    tags = columns.TagColumn(url_name="plugins:netbox_support_contracts:supportcoverage_list")

    class Meta(NetBoxTable.Meta):
        model = SupportCoverage
        fields = (
            "pk", "id", "contract", "contract_type", "manufacturer", "reseller", "device", "device_serial",
            "device_type", "site", "virtual_machine", "module", "sku", "quantity", "start_date", "effective_end_date",
            "end_date", "state", "days_remaining", "notes", "tags", "created", "last_updated", "actions",
        )
        default_columns = (
            "pk", "contract", "reseller", "device", "device_serial", "device_type", "site", "virtual_machine", "sku",
            "quantity", "start_date", "effective_end_date", "state", "days_remaining", "actions",
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


#
# Columns on NetBox's core tables (Devices, Virtual Machines, Modules, Device/Module Types)
#

def _request_user_may(permission):
    request = current_request.get()
    user = getattr(request, "user", None)
    return bool(user is not None and user.has_perm(f"netbox_support_contracts.{permission}"))


def _coverage_rows(record):
    """The record's prefetched coverage rows, or None when the user may not view coverage."""
    if not _request_user_may("view_supportcoverage"):
        return None
    return list(record.support_coverage.all())


def _latest_end(rows):
    """Latest effective end date across coverage rows: None = not covered, OPEN_ENDED = open-ended."""
    if not rows:
        return None
    return max(row.end_date or row.contract.end_date or OPEN_ENDED for row in rows)


class CoreCoverageColumn(tables.Column):
    """Base for columns added to core tables; prefetches support_coverage__contract when enabled."""

    empty_values = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, accessor=tables.A("support_coverage__contract"), orderable=False, **kwargs)

    def render(self, record):
        rows = _coverage_rows(record)
        return "—" if rows is None else self.render_rows(rows)

    def value(self, record):
        rows = _coverage_rows(record)
        return "" if rows is None else self.value_rows(rows)


class SupportCoverageStatusColumn(CoreCoverageColumn):
    def render_rows(self, rows):
        return coverage_badge(coverage_status(_latest_end(rows)))

    def value_rows(self, rows):
        return str(dict(CoverageStatusChoices)[coverage_status(_latest_end(rows))])


class SupportEndDateColumn(CoreCoverageColumn):
    def render_rows(self, rows):
        latest = _latest_end(rows)
        if latest is None:
            return "—"
        return _("Open-ended") if latest == OPEN_ENDED else latest.isoformat()

    def value_rows(self, rows):
        latest = _latest_end(rows)
        return "" if latest in (None, OPEN_ENDED) else latest.isoformat()


class SupportDaysRemainingColumn(CoreCoverageColumn):
    def render_rows(self, rows):
        latest = _latest_end(rows)
        if latest is None:
            return "—"
        return days_badge(None if latest == OPEN_ENDED else days_until(latest))

    def value_rows(self, rows):
        latest = _latest_end(rows)
        return "" if latest in (None, OPEN_ENDED) else days_until(latest)


class SupportResellerColumn(CoreCoverageColumn):
    @staticmethod
    def resellers(rows):
        """Distinct resellers of the contracts still covering the object (not expired), in first-seen order."""
        names = []
        for row in rows:
            if row.state != ContractStateChoices.EXPIRED and row.contract.reseller not in names:
                names.append(row.contract.reseller)
        return ", ".join(name for name in names if name)

    def render_rows(self, rows):
        return self.resellers(rows) or "—"

    def value_rows(self, rows):
        return self.resellers(rows)


class EndOfSupportColumn(tables.Column):
    """End of Support date and support state of a device/module type's lifecycle record."""

    empty_values = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, accessor=tables.A("support_eol"), orderable=False, **kwargs)

    @staticmethod
    def end_of_support(record):
        if not _request_user_may("view_hardwarelifecycleeol"):
            return None
        try:
            return record.support_eol.end_of_support
        except ObjectDoesNotExist:
            return None

    def render(self, record):
        eos = self.end_of_support(record)
        if eos is None:
            return "—"
        return format_html("{} {}", eos.isoformat(), state_badge(contract_state(eos)))

    def value(self, record):
        eos = self.end_of_support(record)
        return eos.isoformat() if eos else ""


CORE_COVERAGE_COLUMNS = {
    "support_coverage_status": (SupportCoverageStatusColumn, _("Support Coverage")),
    "support_end_date": (SupportEndDateColumn, _("Support End Date")),
    "support_days_remaining": (SupportDaysRemainingColumn, _("Support Days Remaining")),
    "support_reseller": (SupportResellerColumn, _("Support Reseller")),
}


def register_core_columns():
    """Offer the plugin's columns under "Configure Table" on NetBox's core tables. Safe to call repeatedly."""
    from dcim.tables import DeviceTable, DeviceTypeTable, ModuleTable, ModuleTypeTable
    from netbox.registry import registry
    from utilities.tables import register_table_column
    from virtualization.tables import VirtualMachineTable

    def register(column, name, table):
        if name not in registry["tables"][table]:
            register_table_column(column, name, table)

    for table in (DeviceTable, VirtualMachineTable, ModuleTable):
        for name, (column_class, label) in CORE_COVERAGE_COLUMNS.items():
            register(column_class(verbose_name=label), name, table)
    for table in (DeviceTypeTable, ModuleTypeTable):
        register(EndOfSupportColumn(verbose_name=_("End of Support")), "end_of_support", table)
