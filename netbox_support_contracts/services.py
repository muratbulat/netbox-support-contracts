"""
Coverage rules: state calculation, SQL annotations, serial matching, bulk coverage, and EoL conflict flags.

Nothing here is stored: contract/coverage state is always derived from end dates at read time, so it can never go
stale. Model imports are deferred to function bodies because ``models`` imports this module.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Count, DateField, IntegerField, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Upper
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext, pgettext
from netbox.plugins import get_plugin_config
from utilities.exceptions import PermissionsViolation

from .choices import ContractStateChoices, CoverageStatusChoices

__all__ = (
    "MAX_BULK_ITEMS",
    "OPEN_ENDED",
    "VIEW_COST_PERMISSION",
    "BulkAddResult",
    "annotate_companies",
    "annotate_contracts",
    "annotate_coverage",
    "annotate_devices",
    "bulk_add_coverage",
    "bulk_remove_coverage",
    "contract_state",
    "contract_state_q",
    "coverage_status",
    "coverage_status_q",
    "coverage_summary",
    "days_until",
    "days_label",
    "effective_integrator_q",
    "integrator_names",
    "maintenance_company_names",
    "expiry_horizon",
    "get_warning_days",
    "lifecycle_flags",
    "match_serials",
    "may_view_cost",
    "object_coverage",
    "parse_serials",
    "provider_export_headers",
    "provider_export_rows",
)

PLUGIN = "netbox_support_contracts"

# ObjectPermission form: object type "Support Contract", additional action "view_cost".
VIEW_COST_PERMISSION = f"{PLUGIN}.view_cost_supportcontract"

# Sentinel "end date" for open-ended contracts: sorts last when ordering by soonest expiration and wins any Max().
OPEN_ENDED = date(9999, 12, 31)

# Upper bound for one bulk coverage operation (pasted serials or filter-selected devices).
MAX_BULK_ITEMS = 5000

SERIAL_SPLIT_RE = re.compile(r"[\s,;]+")

# Reverse accessor on SupportCoverage for each coverable model, keyed by "app_label.model_name".
COVERAGE_FIELDS = {
    "dcim.device": "device",
    "virtualization.virtualmachine": "virtual_machine",
    "dcim.module": "module",
}


#
# Settings & permissions
#

def get_warning_days():
    """The configured "expiring" horizon in days (non-negative int; falls back to 90 on bad config)."""
    try:
        return max(int(get_plugin_config(PLUGIN, "warning_days", 90)), 0)
    except (TypeError, ValueError):
        return 90


def may_view_cost(user):
    """
    True if the user may view (and edit) a contract's commercial cost.

    NetBox registers SupportContract's Meta permission "view_cost" as a custom ObjectPermission action, which
    resolves to VIEW_COST_PERMISSION. The plain Django codename is honoured too, for auth backends that use it.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return bool(
        user.is_superuser or user.has_perm(VIEW_COST_PERMISSION) or user.has_perm(f"{PLUGIN}.view_cost")
    )


#
# State calculation
#

def _today(today=None):
    return today or timezone.localdate()


def expiry_horizon(today, warning_days):
    """today + warning_days, clamped so that absurd values cannot overflow date arithmetic."""
    return today + timedelta(days=min(warning_days, (OPEN_ENDED - today).days))


def days_until(end_date, today=None):
    """Days from today until end_date (0 = ends today, negative = past). None for open-ended."""
    if end_date is None:
        return None
    return (end_date - _today(today)).days


def days_label(days):
    """Human-readable days remaining in the active language (None = open-ended)."""
    if days is None:
        return _("Open-ended")
    if days < 0:
        return ngettext("Expired %(n)d day ago", "Expired %(n)d days ago", -days) % {"n": -days}
    if days == 0:
        return _("Ends today")
    return ngettext("%(n)d day left", "%(n)d days left", days) % {"n": days}


def contract_state(end_date, warning_days=None, today=None):
    """
    "active", "expiring", or "expired" for a contract ending on end_date (the last covered day).

    An open-ended contract (end_date None) is always active. A contract ending today is still covered, so it is
    "expiring", not "expired".
    """
    if end_date is None:
        return ContractStateChoices.ACTIVE
    warning_days = get_warning_days() if warning_days is None else warning_days
    remaining = days_until(end_date, today)
    if remaining < 0:
        return ContractStateChoices.EXPIRED
    if remaining <= warning_days:
        return ContractStateChoices.EXPIRING
    return ContractStateChoices.ACTIVE


def coverage_status(end_date, warning_days=None, today=None):
    """
    "covered", "expiring", "expired", or "none" for an object whose best coverage ends on end_date.

    Here None means *no coverage at all*; open-ended coverage is represented by OPEN_ENDED (as produced by
    annotate_devices()).
    """
    if end_date is None:
        return CoverageStatusChoices.NONE
    state = contract_state(end_date, warning_days, today)
    return CoverageStatusChoices.COVERED if state == ContractStateChoices.ACTIVE else state


def contract_state_q(state, warning_days=None, today=None, field="end_date"):
    """A Q filter selecting rows whose end date field (None = open-ended) is in the given contract state."""
    today = _today(today)
    warning_days = get_warning_days() if warning_days is None else warning_days
    horizon = expiry_horizon(today, warning_days)
    if state == ContractStateChoices.ACTIVE:
        return Q(**{f"{field}__isnull": True}) | Q(**{f"{field}__gt": horizon})
    if state == ContractStateChoices.EXPIRING:
        return Q(**{f"{field}__gte": today, f"{field}__lte": horizon})
    if state == ContractStateChoices.EXPIRED:
        return Q(**{f"{field}__lt": today})
    raise ValueError(f"Unknown contract state: {state!r}")


def coverage_status_q(status, warning_days=None, today=None, field="coverage_end_annotated"):
    """A Q filter on annotate_devices() output (None = not covered, OPEN_ENDED = open-ended)."""
    if status == CoverageStatusChoices.NONE:
        return Q(**{f"{field}__isnull": True})
    if status == CoverageStatusChoices.COVERED:
        today = _today(today)
        horizon = expiry_horizon(today, get_warning_days() if warning_days is None else warning_days)
        return Q(**{f"{field}__gt": horizon})
    if status in (CoverageStatusChoices.EXPIRING, CoverageStatusChoices.EXPIRED):
        return contract_state_q(status, warning_days, today, field) & Q(**{f"{field}__isnull": False})
    raise ValueError(f"Unknown coverage status: {status!r}")


#
# SQL annotations
#

def _coverage_field(model):
    try:
        return COVERAGE_FIELDS[model._meta.label_lower]
    except KeyError:
        raise ValueError(f"{model._meta.label} cannot be covered by a support contract") from None


def _coverage_count(**lookups):
    from .models import SupportCoverage

    rows = SupportCoverage.objects.filter(contract=OuterRef("pk"), **lookups)
    count = rows.order_by().values("contract").annotate(c=Count("pk")).values("c")
    return Coalesce(Subquery(count, output_field=IntegerField()), 0)


def annotate_contracts(qs):
    """
    Annotate a SupportContract queryset with device_count, vm_count, module_count, sku_count (contract-level
    license/SKU rows not tied to an object), and effective_sort_end (open-ended last) using correlated
    subqueries, so a whole page costs a single query.
    """
    return qs.annotate(
        device_count=_coverage_count(device__isnull=False),
        vm_count=_coverage_count(virtual_machine__isnull=False),
        module_count=_coverage_count(module__isnull=False),
        sku_count=_coverage_count(device__isnull=True, virtual_machine__isnull=True, module__isnull=True),
        effective_sort_end=Coalesce("end_date", Value(OPEN_ENDED)),
    )


def annotate_coverage(qs):
    """
    Annotate a SupportCoverage queryset with effective_start / effective_end (row override, else the contract's
    dates; a None end = open-ended).
    """
    return qs.annotate(
        effective_start=Coalesce("start_date", "contract__start_date"),
        effective_end=Coalesce("end_date", "contract__end_date"),
    )


class DistinctDeviceCount(Subquery):
    """Number of distinct devices in a correlated SupportCoverage subquery (selecting device_id)."""

    template = "(SELECT COUNT(DISTINCT _rows.device_id) FROM (%(subquery)s) _rows)"
    output_field = IntegerField()


def effective_integrator_q(integrators, prefix=""):
    """Coverage rows whose effective integrator (row override, else the contract's) is in `integrators`."""
    return Q(**{f"{prefix}integrator__in": integrators}) | Q(
        **{f"{prefix}integrator__isnull": True, f"{prefix}contract__integrator__in": integrators}
    )


def annotate_companies(qs):
    """Annotate an Integrator or MaintenanceCompany queryset with contract_count and (covered) device_count."""
    from utilities.query import count_related

    from .models import Integrator, SupportContract, SupportCoverage

    rows = SupportCoverage.objects.filter(device__isnull=False)
    if qs.model is Integrator:
        field = "integrator"
        rows = rows.filter(
            Q(integrator=OuterRef("pk")) | Q(integrator__isnull=True, contract__integrator=OuterRef("pk"))
        )
    else:
        field = "maintenance_company"
        rows = rows.filter(contract__maintenance_company=OuterRef("pk"))
    return qs.annotate(
        contract_count=count_related(SupportContract, field),
        device_count=DistinctDeviceCount(rows.order_by().values("device_id")),
    )


def annotate_devices(qs):
    """
    Annotate a Device, VirtualMachine, or Module queryset with:

    * coverage_end_annotated: the latest effective coverage end date across all covering contracts (OPEN_ENDED if
      any coverage is open-ended, None if the object is not covered at all)
    * coverage_contracts_annotated: the number of distinct covering contracts
    """
    from .models import SupportCoverage

    field = _coverage_field(qs.model)
    rows = SupportCoverage.objects.filter(**{field: OuterRef("pk")}).order_by().values(field)
    latest_end = rows.annotate(m=Max(Coalesce("end_date", "contract__end_date", Value(OPEN_ENDED)))).values("m")
    contracts = rows.annotate(c=Count("contract", distinct=True)).values("c")
    return qs.annotate(
        coverage_end_annotated=Subquery(latest_end, output_field=DateField()),
        coverage_contracts_annotated=Coalesce(Subquery(contracts, output_field=IntegerField()), 0),
    )


#
# Coverage of a single object & dashboard summary
#

def _eol_record(obj):
    """The HardwareLifecycleEoL record for obj's device type / module type, or None."""
    for type_attr in ("device_type", "module_type"):
        hardware_type = getattr(obj, type_attr, None)
        if hardware_type is not None:
            try:
                return hardware_type.support_eol
            except ObjectDoesNotExist:
                return None
    return None


def lifecycle_flags(device, contract_end, has_active_contract, today=None):
    """
    Conflicts between support coverage and the vendor End of Support date of the object's hardware type.

    Returns a list of {"code", "message", "end_of_support"} dicts:

    * eol_under_contract: hardware is past End of Support but still under an active contract
    * contract_after_eol: the support contract runs past the hardware's End of Support
    """
    eol = _eol_record(device)
    if eol is None or eol.end_of_support is None:
        return []
    eos = eol.end_of_support
    today = _today(today)
    flags = []
    if eos <= today and has_active_contract:
        flags.append({
            "code": "eol_under_contract",
            "message": _("End-of-life hardware under active support contract"),
            "end_of_support": eos,
        })
    if contract_end is not None and contract_end > eos:
        flags.append({
            "code": "contract_after_eol",
            "message": _("Support contract runs past hardware End of Support (EoS)"),
            "end_of_support": eos,
        })
    return flags


def object_coverage(obj, user=None, today=None, warning_days=None):
    """
    Everything the device/VM detail panel needs: covering rows (restricted to what the user may view), the overall
    coverage status, the effective end date (None = open-ended or not covered; see "status"), and EoL flags.
    """
    from .models import SupportCoverage

    today = _today(today)
    warning_days = get_warning_days() if warning_days is None else warning_days
    field = _coverage_field(type(obj))
    rows = SupportCoverage.objects.filter(**{field: obj}).select_related(
        "contract__manufacturer", "contract__integrator", "contract__maintenance_company", "integrator"
    )
    if user is not None:
        rows = rows.restrict(user, "view")
    rows = sorted(rows, key=lambda r: r.effective_end_date or OPEN_ENDED, reverse=True)
    latest = (rows[0].effective_end_date or OPEN_ENDED) if rows else None
    end_date = None if latest in (None, OPEN_ENDED) else latest
    has_active = any(
        contract_state(r.effective_end_date, warning_days, today) != ContractStateChoices.EXPIRED for r in rows
    )
    return {
        "rows": rows,
        "status": coverage_status(latest, warning_days, today),
        "end_date": end_date,
        "open_ended": latest == OPEN_ENDED,
        "days_remaining": days_until(end_date, today),
        "flags": lifecycle_flags(obj, end_date if latest != OPEN_ENDED else None, has_active, today),
    }


def coverage_summary(user=None, today=None, warning_days=None, limit=8):
    """
    Dashboard numbers: device counts per coverage status plus the soonest-ending (not yet expired) contracts,
    all restricted to the objects the user may view.
    """
    from dcim.models import Device

    from .models import SupportContract

    today = _today(today)
    warning_days = get_warning_days() if warning_days is None else warning_days
    devices = Device.objects.all()
    contracts = SupportContract.objects.select_related("manufacturer", "maintenance_company")
    if user is not None:
        devices = devices.restrict(user, "view")
        contracts = contracts.restrict(user, "view")
    counts = annotate_devices(devices).aggregate(
        total=Count("pk"),
        **{
            status: Count("pk", filter=coverage_status_q(status, warning_days, today))
            for status in CoverageStatusChoices.values()
        },
    )
    soonest = annotate_contracts(contracts).filter(end_date__gte=today).order_by("end_date", "pk")[:limit]
    return {
        "warning_days": warning_days,
        "devices": counts,
        "soonest_contracts": list(soonest),
    }


#
# Serial number matching
#

def parse_serials(text):
    """
    Split pasted text (newlines, commas, semicolons, or whitespace) into serial numbers, de-duplicated
    case-insensitively while preserving first-seen order and spelling.
    """
    serials, seen = [], set()
    for token in SERIAL_SPLIT_RE.split(text or ""):
        key = token.upper()
        if token and key not in seen:
            seen.add(key)
            serials.append(token)
    return serials


def match_serials(device_qs, serials):
    """
    Match serials case-insensitively against device_qs in a single query.

    Returns {"devices": [...], "unmatched": [...], "ambiguous": [...]}. A serial shared by more than one device is
    reported as ambiguous and never guessed.
    """
    wanted = {}
    for serial in serials:
        wanted.setdefault(serial.upper(), serial)
    hits = defaultdict(list)
    if wanted:
        for device in device_qs.annotate(_serial_u=Upper("serial")).filter(_serial_u__in=list(wanted)):
            hits[device._serial_u].append(device)
    result = {"devices": [], "unmatched": [], "ambiguous": []}
    for key, serial in wanted.items():
        matched = hits.get(key, [])
        if len(matched) == 1:
            result["devices"].append(matched[0])
        elif matched:
            result["ambiguous"].append(serial)
        else:
            result["unmatched"].append(serial)
    return result


#
# Bulk coverage
#

@dataclass
class BulkAddResult:
    created: list = field(default_factory=list)
    existing: list = field(default_factory=list)
    matched: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)

    @property
    def needs_review(self):
        return bool(self.unmatched or self.ambiguous)


def _unique(objects):
    seen, result = set(), []
    for obj in objects:
        if obj.pk not in seen:
            seen.add(obj.pk)
            result.append(obj)
    return result


def bulk_add_coverage(
    contract,
    *,
    user,
    devices=(),
    virtual_machines=(),
    modules=(),
    serials=(),
    sku="",
    quantity=None,
    start_date=None,
    end_date=None,
    integrator=None,
    notes="",
):
    """
    Cover objects with a contract in one transaction, skipping objects it already covers.

    Serials are matched against the devices the user may view. Every created row is change-logged, and the whole
    operation is rolled back if any row violates the user's object-level "add" permission constraints.
    """
    from dcim.models import Device

    from .models import SupportCoverage

    serials = list(serials)
    if len(serials) > MAX_BULK_ITEMS:
        raise ValidationError(_("At most {max} serial numbers can be processed at once.").format(max=MAX_BULK_ITEMS))
    result = BulkAddResult()
    if serials:
        matches = match_serials(Device.objects.restrict(user, "view"), serials)
        result.matched = matches["devices"]
        result.unmatched = matches["unmatched"]
        result.ambiguous = matches["ambiguous"]

    targets = {
        "device": _unique([*devices, *result.matched]),
        "virtual_machine": _unique(virtual_machines),
        "module": _unique(modules),
    }
    if sum(len(objs) for objs in targets.values()) > MAX_BULK_ITEMS:
        raise ValidationError(_("At most {max} objects can be covered at once.").format(max=MAX_BULK_ITEMS))

    with transaction.atomic():
        for field_name, objects in targets.items():
            if not objects:
                continue
            covered = set(
                SupportCoverage.objects.filter(
                    contract=contract, **{f"{field_name}__in": [obj.pk for obj in objects]}
                ).values_list(f"{field_name}_id", flat=True)
            )
            for obj in objects:
                if obj.pk in covered:
                    result.existing.append(obj)
                    continue
                coverage = SupportCoverage(
                    contract=contract, sku=sku, quantity=quantity, start_date=start_date, end_date=end_date,
                    integrator=integrator, notes=notes,
                )
                setattr(coverage, field_name, obj)
                coverage.full_clean()
                coverage.save()
                result.created.append(coverage)

        created_pks = [coverage.pk for coverage in result.created]
        permitted = SupportCoverage.objects.restrict(user, "add").filter(pk__in=created_pks).count()
        if permitted != len(created_pks):
            raise PermissionsViolation()
    return result


def bulk_remove_coverage(contract, *, user, coverage_ids=(), device_ids=(), virtual_machine_ids=(), module_ids=()):
    """
    Delete the contract's coverage rows selected by row ID and/or covered object ID. Rows the user cannot view are
    ignored; if any visible row may not be deleted, nothing is deleted. Returns the number of rows removed.
    """
    from .models import SupportCoverage

    selector = (
        Q(pk__in=list(coverage_ids))
        | Q(device_id__in=list(device_ids))
        | Q(virtual_machine_id__in=list(virtual_machine_ids))
        | Q(module_id__in=list(module_ids))
    )
    visible = SupportCoverage.objects.restrict(user, "view").filter(selector, contract=contract)
    with transaction.atomic():
        rows = list(visible)
        deletable = visible.restrict(user, "delete").count()
        if deletable != len(rows):
            raise PermissionsViolation()
        for row in rows:
            row.delete()
    return len(rows)


#
# Partner companies of an object's coverage
#

def _distinct_names(objects):
    names = []
    for obj in objects:
        if obj is not None and obj.name not in names:
            names.append(obj.name)
    return ", ".join(names)


def integrator_names(rows):
    """
    Integrators the object was bought through, across all coverage rows: purchase history stays relevant after the
    OEM warranty has expired.
    """
    return _distinct_names(row.effective_integrator for row in rows)


def maintenance_company_names(rows, today=None):
    """Maintenance companies of the contracts still covering the object (expired contracts no longer do)."""
    return _distinct_names(
        row.contract.maintenance_company
        for row in rows
        if contract_state(row.effective_end_date, today=today) != ContractStateChoices.EXPIRED
    )


#
# Support provider / RFQ export
#

def provider_export_headers():
    """Column headers of the provider-ready device export, in the active language."""
    return [
        pgettext("provider export", "Manufacturer"),
        pgettext("provider export", "Device Type"),
        pgettext("provider export", "Part Number"),
        pgettext("provider export", "Model Device Count"),
        pgettext("provider export", "Device"),
        pgettext("provider export", "Serial Number"),
        pgettext("provider export", "Asset Tag"),
        pgettext("provider export", "Site"),
        pgettext("provider export", "Rack"),
        pgettext("provider export", "Role"),
        pgettext("provider export", "Device Status"),
        pgettext("provider export", "Coverage Status"),
        pgettext("provider export", "Current Contract"),
        pgettext("provider export", "Current Contract Type"),
        pgettext("provider export", "Integrator Company"),
        pgettext("provider export", "Maintenance Company"),
        pgettext("provider export", "Current Coverage End Date"),
        pgettext("provider export", "Days Remaining"),
        pgettext("provider export", "Hardware End of Support (EoS)"),
    ]


def provider_export_rows(device_qs, user, today=None):
    """
    One row per device for support providers quoting a renewal or takeover: hardware identity (model, part number,
    serial, per-model unit count), location, and the coverage currently in force.

    "Current" coverage is the visible coverage row with the latest effective end date, i.e. the one that determines
    when the device's support actually ends. Coverage and lifecycle data the user may not view is left blank.
    """
    from collections import Counter

    from django.db.models import Prefetch

    from .choices import ContractTypeChoices
    from .models import SupportCoverage

    today = _today(today)
    coverage = SupportCoverage.objects.select_related(
        "contract__integrator", "contract__maintenance_company", "integrator"
    )
    coverage = coverage.restrict(user, "view") if user.has_perm(f"{PLUGIN}.view_supportcoverage") else coverage.none()
    show_eol = user.has_perm(f"{PLUGIN}.view_hardwarelifecycleeol")
    devices = list(
        device_qs.select_related("device_type__manufacturer", "device_type__support_eol", "site", "rack", "role")
        .prefetch_related(Prefetch("support_coverage", queryset=coverage))
        .order_by("device_type__manufacturer__name", "device_type__model", "name", "pk")
    )
    per_model = Counter(device.device_type_id for device in devices)
    coverage_labels = dict(CoverageStatusChoices)
    type_labels = dict(ContractTypeChoices)
    rows = []
    for device in devices:
        current = max(
            device.support_coverage.all(), key=lambda row: row.effective_end_date or OPEN_ENDED, default=None
        )
        latest = (current.effective_end_date or OPEN_ENDED) if current else None
        if latest is None:
            end_date = ""
        elif latest == OPEN_ENDED:
            end_date = _("Open-ended")
        else:
            end_date = latest.isoformat()
        eos = None
        if show_eol:
            try:
                eos = device.device_type.support_eol.end_of_support
            except ObjectDoesNotExist:
                eos = None
        rows.append([
            device.device_type.manufacturer.name,
            device.device_type.model,
            device.device_type.part_number,
            per_model[device.device_type_id],
            device.name or "",
            device.serial,
            device.asset_tag or "",
            device.site.name,
            device.rack.name if device.rack else "",
            device.role.name,
            device.get_status_display(),
            coverage_labels[coverage_status(latest, today=today)],
            current.contract.contract_number if current else "",
            type_labels[current.contract.contract_type] if current else "",
            integrator_names(device.support_coverage.all()),
            maintenance_company_names(device.support_coverage.all(), today),
            end_date,
            days_until(latest, today) if latest not in (None, OPEN_ENDED) else "",
            eos.isoformat() if eos else "",
        ])
    return rows
