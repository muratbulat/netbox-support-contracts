import django_filters
from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleType, Site
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from netbox.filtersets import NetBoxModelFilterSet, PrimaryModelFilterSet
from tenancy.models import Tenant
from utilities.filtersets import register_filterset
from virtualization.models import VirtualMachine

from .choices import ContractStateChoices, ContractTypeChoices, CoverageStatusChoices
from .models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from .services import (
    OPEN_ENDED,
    annotate_coverage,
    annotate_devices,
    contract_state_q,
    coverage_status_q,
    expiry_horizon,
)

__all__ = (
    "DeviceCoverageFilterSet",
    "HardwareLifecycleEoLFilterSet",
    "SupportContractFilterSet",
    "SupportCoverageFilterSet",
)


def filter_states(queryset, field, values):
    """OR together contract_state_q() for each selected state."""
    if not values:
        return queryset
    query = Q()
    for value in values:
        query |= contract_state_q(value, field=field)
    return queryset.filter(query)


@register_filterset
class SupportContractFilterSet(PrimaryModelFilterSet):
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Manufacturer.objects.all(), label=_("Manufacturer (ID)")
    )
    manufacturer = django_filters.ModelMultipleChoiceFilter(
        field_name="manufacturer__slug",
        queryset=Manufacturer.objects.all(),
        to_field_name="slug",
        label=_("Manufacturer (slug)"),
    )
    tenant_id = django_filters.ModelMultipleChoiceFilter(queryset=Tenant.objects.all(), label=_("Tenant (ID)"))
    tenant = django_filters.ModelMultipleChoiceFilter(
        field_name="tenant__slug", queryset=Tenant.objects.all(), to_field_name="slug", label=_("Tenant (slug)")
    )
    contract_type = django_filters.MultipleChoiceFilter(choices=ContractTypeChoices, label=_("Contract type"))
    state = django_filters.MultipleChoiceFilter(
        choices=ContractStateChoices, method="filter_state", label=pgettext_lazy("support contracts", "State")
    )
    device_id = django_filters.ModelMultipleChoiceFilter(
        field_name="coverage__device",
        queryset=Device.objects.all(),
        distinct=True,
        label=_("Covered device (ID)"),
    )
    virtual_machine_id = django_filters.ModelMultipleChoiceFilter(
        field_name="coverage__virtual_machine",
        queryset=VirtualMachine.objects.all(),
        distinct=True,
        label=_("Covered virtual machine (ID)"),
    )
    module_id = django_filters.ModelMultipleChoiceFilter(
        field_name="coverage__module",
        queryset=Module.objects.all(),
        distinct=True,
        label=_("Covered module (ID)"),
    )
    expiring_within_days = django_filters.NumberFilter(
        method="filter_expiring_within_days", label=_("Ends within N days (not yet expired)")
    )

    class Meta:
        model = SupportContract
        # Cost/currency are deliberately not filterable: filtering would leak values gated by view_cost.
        fields = (
            "id", "contract_number", "service_level", "start_date", "end_date", "reseller", "portal_url",
            "description",
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(contract_number__icontains=value)
            | Q(service_level__icontains=value)
            | Q(reseller__icontains=value)
            | Q(description__icontains=value)
            | Q(comments__icontains=value)
            | Q(manufacturer__name__icontains=value)
        )

    def filter_state(self, queryset, name, value):
        return filter_states(queryset, "end_date", value)

    def filter_expiring_within_days(self, queryset, name, value):
        if value is None or value < 0:
            return queryset
        today = timezone.localdate()
        return queryset.filter(end_date__gte=today, end_date__lte=expiry_horizon(today, int(value)))


@register_filterset
class SupportCoverageFilterSet(NetBoxModelFilterSet):
    contract_id = django_filters.ModelMultipleChoiceFilter(
        queryset=SupportContract.objects.all(), label=_("Contract (ID)")
    )
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        field_name="contract__manufacturer",
        queryset=Manufacturer.objects.all(),
        label=_("Manufacturer (ID)"),
    )
    device_id = django_filters.ModelMultipleChoiceFilter(queryset=Device.objects.all(), label=_("Device (ID)"))
    virtual_machine_id = django_filters.ModelMultipleChoiceFilter(
        queryset=VirtualMachine.objects.all(), label=_("Virtual machine (ID)")
    )
    module_id = django_filters.ModelMultipleChoiceFilter(queryset=Module.objects.all(), label=_("Module (ID)"))
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name="device__site", queryset=Site.objects.all(), label=_("Site (ID)")
    )
    contract_type = django_filters.MultipleChoiceFilter(
        field_name="contract__contract_type", choices=ContractTypeChoices, label=_("Contract type")
    )
    reseller = django_filters.CharFilter(field_name="contract__reseller", lookup_expr="icontains", label=_("Reseller"))
    state = django_filters.MultipleChoiceFilter(
        choices=ContractStateChoices, method="filter_state", label=pgettext_lazy("support contracts", "State")
    )
    object_type = django_filters.ChoiceFilter(
        choices=(
            ("device", _("Device")),
            ("virtual_machine", _("Virtual machine")),
            ("module", _("Module")),
            ("none", _("Contract-level (SKU only)")),
        ),
        method="filter_object_type",
        label=_("Covered object type"),
    )

    class Meta:
        model = SupportCoverage
        fields = ("id", "sku", "quantity", "notes", "end_date")

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(sku__icontains=value)
            | Q(notes__icontains=value)
            | Q(contract__contract_number__icontains=value)
            | Q(contract__reseller__icontains=value)
            | Q(device__name__icontains=value)
            | Q(device__serial__iexact=value.strip())
            | Q(virtual_machine__name__icontains=value)
            | Q(module__serial__iexact=value.strip())
        )

    def filter_state(self, queryset, name, value):
        if not value:
            return queryset
        return filter_states(annotate_coverage(queryset), "effective_end", value)

    def filter_object_type(self, queryset, name, value):
        if value == "none":
            return queryset.filter(device__isnull=True, virtual_machine__isnull=True, module__isnull=True)
        if value in SupportCoverage.OBJECT_FIELDS:
            return queryset.filter(**{f"{value}__isnull": False})
        return queryset


@register_filterset
class HardwareLifecycleEoLFilterSet(PrimaryModelFilterSet):
    device_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=DeviceType.objects.all(), label=_("Device type (ID)")
    )
    module_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=ModuleType.objects.all(), label=_("Module type (ID)")
    )
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Manufacturer.objects.all(), method="filter_manufacturer", label=_("Manufacturer (ID)")
    )
    state = django_filters.MultipleChoiceFilter(
        choices=ContractStateChoices, method="filter_state", label=_("Support state")
    )

    class Meta:
        model = HardwareLifecycleEoL
        fields = ("id", "end_of_sale", "end_of_maintenance", "end_of_support", "notice_url", "description")

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(device_type__model__icontains=value)
            | Q(device_type__part_number__icontains=value)
            | Q(module_type__model__icontains=value)
            | Q(module_type__part_number__icontains=value)
            | Q(description__icontains=value)
            | Q(comments__icontains=value)
        )

    def filter_manufacturer(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(Q(device_type__manufacturer__in=value) | Q(module_type__manufacturer__in=value))

    def filter_state(self, queryset, name, value):
        return filter_states(queryset, "end_of_support", value)


class DeviceCoverageFilterSet(django_filters.FilterSet):
    """Device-side coverage filters for the plugin's Device Coverage report (not registered for dcim.Device)."""

    q = django_filters.CharFilter(method="search", label=_("Search"))
    coverage = django_filters.MultipleChoiceFilter(
        choices=CoverageStatusChoices, method="filter_coverage", label=_("Coverage")
    )
    site_id = django_filters.ModelMultipleChoiceFilter(queryset=Site.objects.all(), label=_("Site (ID)"))
    role_id = django_filters.ModelMultipleChoiceFilter(queryset=DeviceRole.objects.all(), label=_("Role (ID)"))
    device_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=DeviceType.objects.all(), label=_("Device type (ID)")
    )
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        field_name="device_type__manufacturer", queryset=Manufacturer.objects.all(), label=_("Manufacturer (ID)")
    )
    contract_type = django_filters.MultipleChoiceFilter(
        choices=ContractTypeChoices, method="filter_contract", label=_("Contract type")
    )
    reseller = django_filters.CharFilter(method="filter_contract", label=_("Reseller"))
    contract_id = django_filters.ModelMultipleChoiceFilter(
        queryset=SupportContract.objects.all(), method="filter_contract", label=_("Contract")
    )
    status = django_filters.MultipleChoiceFilter(choices=DeviceStatusChoices, label=_("Device status"))
    expiring_within_days = django_filters.NumberFilter(
        method="filter_expiring_within_days", label=_("Coverage ends within (days)")
    )
    coverage_end_before = django_filters.DateFilter(
        method="filter_coverage_end", label=_("Coverage ends on or before")
    )
    coverage_end_after = django_filters.DateFilter(method="filter_coverage_end", label=_("Coverage ends on or after"))

    class Meta:
        model = Device
        fields = ("id",)

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | Q(serial__iexact=value.strip()) | Q(asset_tag__iexact=value.strip())
        )

    def filter_contract(self, queryset, name, value):
        """Devices covered by at least one contract of the given type(s) / whose reseller contains the value."""
        if not value:
            return queryset
        lookup = {
            "contract_type": "contract__contract_type__in",
            "reseller": "contract__reseller__icontains",
            "contract_id": "contract__in",
        }[name]
        covered = SupportCoverage.objects.filter(device__isnull=False, **{lookup: value}).values("device_id")
        return queryset.filter(pk__in=covered)

    @staticmethod
    def _with_coverage_end(queryset):
        if "coverage_end_annotated" not in queryset.query.annotations:
            queryset = annotate_devices(queryset)
        return queryset.exclude(coverage_end_annotated=OPEN_ENDED).filter(coverage_end_annotated__isnull=False)

    def filter_expiring_within_days(self, queryset, name, value):
        """Devices whose coverage ends between today and today + N days (inclusive); open-ended excluded."""
        if value is None or value < 0:
            return queryset
        today = timezone.localdate()
        return self._with_coverage_end(queryset).filter(
            coverage_end_annotated__gte=today, coverage_end_annotated__lte=expiry_horizon(today, int(value))
        )

    def filter_coverage_end(self, queryset, name, value):
        """Coverage end on/before or on/after a date (open-ended coverage never matches)."""
        if value is None:
            return queryset
        lookup = "lte" if name == "coverage_end_before" else "gte"
        return self._with_coverage_end(queryset).filter(**{f"coverage_end_annotated__{lookup}": value})

    def filter_coverage(self, queryset, name, value):
        if not value:
            return queryset
        if "coverage_end_annotated" not in queryset.query.annotations:
            queryset = annotate_devices(queryset)
        query = Q()
        for status in value:
            query |= coverage_status_q(status)
        return queryset.filter(query)
