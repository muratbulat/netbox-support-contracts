from dcim.api.serializers import (
    DeviceSerializer,
    DeviceTypeSerializer,
    ManufacturerSerializer,
    ModuleSerializer,
    ModuleTypeSerializer,
)
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from netbox.api.fields import ChoiceField
from netbox.api.serializers import NetBoxModelSerializer, PrimaryModelSerializer
from rest_framework import serializers
from tenancy.api.serializers import TenantSerializer
from virtualization.api.serializers import VirtualMachineSerializer

from ..choices import ContractStateChoices, ContractTypeChoices
from ..models import HardwareLifecycleEoL, Integrator, MaintenanceCompany, SupportContract, SupportCoverage
from ..services import MAX_BULK_ITEMS, may_view_cost, parse_serials

__all__ = (
    "BulkAddCoverageSerializer",
    "BulkRemoveCoverageSerializer",
    "HardwareLifecycleEoLSerializer",
    "IntegratorSerializer",
    "MaintenanceCompanySerializer",
    "SupportContractSerializer",
    "SupportCoverageSerializer",
)

COST_FIELDS = ("cost", "currency")


class IntegratorSerializer(PrimaryModelSerializer):
    contract_count = serializers.IntegerField(read_only=True, default=None)
    device_count = serializers.IntegerField(read_only=True, default=None)

    class Meta:
        model = Integrator
        fields = (
            "id", "url", "display_url", "display", "name", "slug", "contact_name", "contact_email", "contact_phone",
            "portal_url", "contract_count", "device_count", "description", "owner", "comments", "tags",
            "custom_fields", "created", "last_updated",
        )
        brief_fields = ("id", "url", "display", "name", "slug", "description")


class MaintenanceCompanySerializer(IntegratorSerializer):
    class Meta(IntegratorSerializer.Meta):
        model = MaintenanceCompany


class SupportContractSerializer(PrimaryModelSerializer):
    manufacturer = ManufacturerSerializer(nested=True, required=False, allow_null=True)
    integrator = IntegratorSerializer(nested=True, required=False, allow_null=True)
    maintenance_company = MaintenanceCompanySerializer(nested=True, required=False, allow_null=True)
    tenant = TenantSerializer(nested=True, required=False, allow_null=True)
    contract_type = ChoiceField(choices=ContractTypeChoices, required=False)
    state = serializers.ChoiceField(choices=ContractStateChoices, read_only=True)
    days_remaining = serializers.IntegerField(read_only=True, allow_null=True)
    device_count = serializers.SerializerMethodField()
    vm_count = serializers.SerializerMethodField()
    module_count = serializers.SerializerMethodField()
    sku_count = serializers.SerializerMethodField()

    class Meta:
        model = SupportContract
        fields = (
            "id", "url", "display_url", "display", "manufacturer", "contract_number", "contract_type", "service_level",
            "start_date", "end_date", "state", "days_remaining", "integrator", "maintenance_company", "cost",
            "currency", "portal_url", "renewal_notes", "tenant", "device_count", "vm_count", "module_count",
            "sku_count", "description", "owner", "comments", "tags", "custom_fields", "created", "last_updated",
        )
        brief_fields = ("id", "url", "display", "contract_number", "end_date", "description")

    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_fields(self):
        # Commercial cost is invisible (and therefore not writable) without netbox_support_contracts.view_cost.
        fields = super().get_fields()
        if not may_view_cost(self._user()):
            for name in COST_FIELDS:
                fields.pop(name, None)
        return fields

    def to_internal_value(self, data):
        if isinstance(data, dict) and not may_view_cost(self._user()):
            denied = [name for name in COST_FIELDS if name in data]
            if denied:
                raise serializers.ValidationError(
                    dict.fromkeys(denied, _("You do not have permission to set the contract cost."))
                )
        return super().to_internal_value(data)

    def _count(self, obj, annotation, **lookups):
        value = getattr(obj, annotation, None)
        if value is None:
            value = obj.coverage.filter(**lookups).count()
        return value

    def get_device_count(self, obj) -> int:
        return self._count(obj, "device_count", device__isnull=False)

    def get_vm_count(self, obj) -> int:
        return self._count(obj, "vm_count", virtual_machine__isnull=False)

    def get_module_count(self, obj) -> int:
        return self._count(obj, "module_count", module__isnull=False)

    def get_sku_count(self, obj) -> int:
        return self._count(
            obj, "sku_count", device__isnull=True, virtual_machine__isnull=True, module__isnull=True
        )


class SupportCoverageSerializer(NetBoxModelSerializer):
    contract = SupportContractSerializer(nested=True)
    device = DeviceSerializer(nested=True, required=False, allow_null=True)
    virtual_machine = VirtualMachineSerializer(nested=True, required=False, allow_null=True)
    module = ModuleSerializer(nested=True, required=False, allow_null=True)
    integrator = IntegratorSerializer(nested=True, required=False, allow_null=True)
    effective_start_date = serializers.DateField(read_only=True, allow_null=True)
    effective_end_date = serializers.DateField(read_only=True, allow_null=True)
    effective_integrator = IntegratorSerializer(nested=True, read_only=True, allow_null=True)
    state = serializers.ChoiceField(choices=ContractStateChoices, read_only=True)
    days_remaining = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = SupportCoverage
        fields = (
            "id", "url", "display_url", "display", "contract", "device", "virtual_machine", "module", "start_date",
            "end_date", "effective_start_date", "effective_end_date", "integrator", "effective_integrator", "state",
            "days_remaining", "sku", "quantity", "notes", "tags", "custom_fields", "created", "last_updated",
        )
        brief_fields = ("id", "url", "display", "contract", "sku")


class HardwareLifecycleEoLSerializer(PrimaryModelSerializer):
    device_type = DeviceTypeSerializer(nested=True, required=False, allow_null=True)
    module_type = ModuleTypeSerializer(nested=True, required=False, allow_null=True)
    state = serializers.ChoiceField(choices=ContractStateChoices, read_only=True)
    days_remaining = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = HardwareLifecycleEoL
        fields = (
            "id", "url", "display_url", "display", "device_type", "module_type", "end_of_sale",
            "end_of_maintenance", "end_of_support", "state", "days_remaining", "notice_url", "description", "owner",
            "comments", "tags", "custom_fields", "created", "last_updated",
        )
        brief_fields = ("id", "url", "display", "end_of_support", "description")


#
# Bulk coverage actions
#

class SerialsField(serializers.Field):
    """Accepts pasted text (newline/comma/semicolon/space separated) or a list of serial strings."""

    default_error_messages = {"invalid": gettext_lazy("Provide a string or a list of strings.")}

    def to_internal_value(self, data):
        if isinstance(data, str):
            return parse_serials(data)
        if isinstance(data, list) and all(isinstance(item, str) for item in data):
            return parse_serials("\n".join(data))
        self.fail("invalid")

    def to_representation(self, value):
        return value


def id_list():
    return serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, default=list, max_length=MAX_BULK_ITEMS
    )


class BulkAddCoverageSerializer(serializers.Serializer):
    contract_id = serializers.IntegerField(min_value=1)
    device_ids = id_list()
    virtual_machine_ids = id_list()
    module_ids = id_list()
    serials = SerialsField(required=False, default=list)
    sku = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=0, required=False, allow_null=True, default=None)
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)
    integrator_id = serializers.IntegerField(min_value=1, required=False, allow_null=True, default=None)
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_serials(self, value):
        if len(value) > MAX_BULK_ITEMS:
            raise serializers.ValidationError(
                _("At most {max} serial numbers can be processed at once.").format(max=MAX_BULK_ITEMS)
            )
        return value

    def validate(self, data):
        if not any(data[name] for name in ("device_ids", "virtual_machine_ids", "module_ids", "serials")):
            raise serializers.ValidationError(
                _("Provide at least one of device_ids, virtual_machine_ids, module_ids, or serials.")
            )
        return data


class BulkRemoveCoverageSerializer(serializers.Serializer):
    contract_id = serializers.IntegerField(min_value=1)
    coverage_ids = id_list()
    device_ids = id_list()
    virtual_machine_ids = id_list()
    module_ids = id_list()

    def validate(self, data):
        if not any(data[name] for name in ("coverage_ids", "device_ids", "virtual_machine_ids", "module_ids")):
            raise serializers.ValidationError(
                _("Provide at least one of coverage_ids, device_ids, virtual_machine_ids, or module_ids.")
            )
        return data
