from dcim.choices import DeviceStatusChoices
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleType, Site
from django import forms
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from netbox.context import current_request
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelFilterSetForm,
    NetBoxModelForm,
    NetBoxModelImportForm,
    PrimaryModelBulkEditForm,
    PrimaryModelFilterSetForm,
    PrimaryModelForm,
    PrimaryModelImportForm,
)
from tenancy.forms import TenancyForm
from tenancy.models import Tenant
from utilities.forms import add_blank_choice
from utilities.forms.fields import (
    CSVChoiceField,
    CSVModelChoiceField,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    SlugField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import DatePicker
from virtualization.models import VirtualMachine

from .choices import ContractStateChoices, ContractTypeChoices, CurrencyChoices
from .models import HardwareLifecycleEoL, Integrator, MaintenanceCompany, SupportContract, SupportCoverage
from .services import MAX_BULK_ITEMS, may_view_cost, parse_serials

__all__ = (
    "BulkCoverageAddForm",
    "DeviceCoverageFilterForm",
    "HardwareLifecycleEoLBulkEditForm",
    "HardwareLifecycleEoLFilterForm",
    "HardwareLifecycleEoLForm",
    "HardwareLifecycleEoLImportForm",
    "IntegratorBulkEditForm",
    "IntegratorFilterForm",
    "IntegratorForm",
    "IntegratorImportForm",
    "MaintenanceCompanyBulkEditForm",
    "MaintenanceCompanyFilterForm",
    "MaintenanceCompanyForm",
    "MaintenanceCompanyImportForm",
    "SupportContractBulkEditForm",
    "SupportContractFilterForm",
    "SupportContractForm",
    "SupportContractImportForm",
    "SupportCoverageBulkEditForm",
    "SupportCoverageFilterForm",
    "SupportCoverageForm",
    "SupportCoverageImportForm",
)

COST_FIELDS = ("cost", "currency")
# Contextual: NetBox's own catalog translates the bare words differently (e.g. "State" as a province).
STATE = pgettext_lazy("support contracts", "State")
DEVICES = pgettext_lazy("support contracts", "Devices")
LIFECYCLE = pgettext_lazy("support contracts", "Lifecycle")
CURRENCY_HELP = format_lazy(
    _("3-letter ISO 4217 code, e.g. {examples}"), examples=", ".join(value for value, _label in CurrencyChoices)
)


class CostPermissionMixin:
    """Drop the commercial cost fields for users without netbox_support_contracts.view_cost."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = current_request.get()
        if not may_view_cost(getattr(request, "user", None)):
            for name in COST_FIELDS:
                self.fields.pop(name, None)
            if hasattr(self, "nullable_fields"):
                self.nullable_fields = tuple(f for f in self.nullable_fields if f not in COST_FIELDS)


def scope_csv_queryset(form, field_name, lookup, value_key):
    """Narrow a CSV lookup field by another column (e.g. contract by manufacturer) when that column is present."""
    value = form.data.get(value_key) if form.data else None
    if value:
        field = form.fields[field_name]
        field.queryset = field.queryset.filter(**{lookup: value})


#
# Support contracts
#

class SupportContractForm(CostPermissionMixin, TenancyForm, PrimaryModelForm):
    manufacturer = DynamicModelChoiceField(
        label=_("Manufacturer"), queryset=Manufacturer.objects.all(), required=False, selector=True, quick_add=True
    )
    integrator = DynamicModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, selector=True,
        quick_add=True,
    )
    maintenance_company = DynamicModelChoiceField(
        label=_("Maintenance company"), queryset=MaintenanceCompany.objects.all(), required=False, selector=True,
        quick_add=True,
    )
    currency = forms.CharField(label=_("Currency"), max_length=3, required=False, help_text=CURRENCY_HELP)

    fieldsets = (
        FieldSet(
            "manufacturer", "contract_number", "contract_type", "service_level", "description", "tags",
            name=_("Contract"),
        ),
        FieldSet("start_date", "end_date", name=_("Term")),
        FieldSet("integrator", "maintenance_company", name=_("Partners & Providers")),
        FieldSet("portal_url", "cost", "currency", "renewal_notes", name=_("Commercial")),
        FieldSet("tenant_group", "tenant", name=_("Tenancy")),
    )

    class Meta:
        model = SupportContract
        fields = (
            "manufacturer", "contract_number", "contract_type", "service_level", "start_date", "end_date",
            "integrator", "maintenance_company", "cost", "currency", "portal_url", "renewal_notes", "tenant_group",
            "tenant", "description", "owner", "comments", "tags",
        )
        widgets = {
            "start_date": DatePicker(),
            "end_date": DatePicker(),
            "renewal_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()


class SupportContractImportForm(CostPermissionMixin, PrimaryModelImportForm):
    manufacturer = CSVModelChoiceField(
        label=_("Manufacturer"),
        queryset=Manufacturer.objects.all(),
        required=False,
        to_field_name="name",
        help_text=_("Manufacturer name"),
    )
    integrator = CSVModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, to_field_name="name",
        help_text=_("Integrator company name"),
    )
    maintenance_company = CSVModelChoiceField(
        label=_("Maintenance company"), queryset=MaintenanceCompany.objects.all(), required=False,
        to_field_name="name", help_text=_("Maintenance company name"),
    )
    contract_type = CSVChoiceField(
        label=_("Contract type"), choices=ContractTypeChoices, required=False, help_text=_("Contract type")
    )
    tenant = CSVModelChoiceField(
        label=_("Tenant"),
        queryset=Tenant.objects.all(),
        required=False,
        to_field_name="name",
        help_text=_("Assigned tenant name"),
    )

    class Meta:
        model = SupportContract
        fields = (
            "manufacturer", "contract_number", "contract_type", "service_level", "start_date", "end_date",
            "integrator", "maintenance_company", "cost", "currency", "portal_url", "renewal_notes", "tenant",
            "description", "owner",
            "comments", "tags",
        )

    def clean_contract_type(self):
        return self.cleaned_data.get("contract_type") or ContractTypeChoices.VENDOR_SUPPORT

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()


class SupportContractBulkEditForm(CostPermissionMixin, PrimaryModelBulkEditForm):
    model = SupportContract
    manufacturer = DynamicModelChoiceField(
        label=_("Manufacturer"), queryset=Manufacturer.objects.all(), required=False
    )
    contract_type = forms.ChoiceField(
        label=_("Contract type"), choices=add_blank_choice(ContractTypeChoices), required=False
    )
    service_level = forms.CharField(label=_("Service level"), max_length=64, required=False)
    start_date = forms.DateField(label=_("Start date"), required=False, widget=DatePicker())
    end_date = forms.DateField(label=_("End date"), required=False, widget=DatePicker())
    integrator = DynamicModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, selector=True,
        quick_add=True,
    )
    maintenance_company = DynamicModelChoiceField(
        label=_("Maintenance company"), queryset=MaintenanceCompany.objects.all(), required=False, selector=True,
        quick_add=True,
    )
    cost = forms.DecimalField(label=_("Cost"), max_digits=12, decimal_places=2, required=False)
    currency = forms.CharField(label=_("Currency"), max_length=3, required=False, help_text=CURRENCY_HELP)
    portal_url = forms.URLField(label=_("Vendor Portal URL"), max_length=500, required=False)
    tenant = DynamicModelChoiceField(label=_("Tenant"), queryset=Tenant.objects.all(), required=False)

    fieldsets = (
        FieldSet("manufacturer", "contract_type", "service_level", "description", name=_("Contract")),
        FieldSet("start_date", "end_date", name=_("Term")),
        FieldSet("integrator", "maintenance_company", name=_("Partners & Providers")),
        FieldSet("portal_url", "cost", "currency", name=_("Commercial")),
        FieldSet("tenant", name=_("Tenancy")),
    )
    nullable_fields = (
        "manufacturer", "service_level", "start_date", "end_date", "integrator", "maintenance_company", "cost",
        "portal_url", "tenant", "description", "comments",
    )

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()


class SupportContractFilterForm(PrimaryModelFilterSetForm):
    model = SupportContract
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet(
            "manufacturer_id", "contract_type", "state", "expiring_within_days", "service_level", name=_("Contract")
        ),
        FieldSet("integrator_id", "maintenance_company_id", name=_("Partners & Providers")),
        FieldSet("device_id", "virtual_machine_id", name=_("Coverage")),
        FieldSet("tenant_id", name=_("Tenancy")),
        FieldSet("owner_group_id", "owner_id", name=_("Ownership")),
    )
    manufacturer_id = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label=_("Manufacturer")
    )
    contract_type = forms.MultipleChoiceField(
        choices=ContractTypeChoices, required=False, label=_("Contract type")
    )
    state = forms.MultipleChoiceField(choices=ContractStateChoices, required=False, label=STATE)
    expiring_within_days = forms.IntegerField(min_value=0, required=False, label=_("Ends within (days)"))
    service_level = forms.CharField(required=False, label=_("Service level"))
    integrator_id = DynamicModelMultipleChoiceField(
        queryset=Integrator.objects.all(), required=False, label=_("Integrator company")
    )
    maintenance_company_id = DynamicModelMultipleChoiceField(
        queryset=MaintenanceCompany.objects.all(), required=False, label=_("Maintenance company")
    )
    device_id = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False, label=_("Device"))
    virtual_machine_id = DynamicModelMultipleChoiceField(
        queryset=VirtualMachine.objects.all(), required=False, label=_("Virtual machine")
    )
    tenant_id = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False, label=_("Tenant"))
    tag = TagFilterField(model)


#
# Partner companies (integrators & maintenance companies)
#

COMPANY_FIELDS = ("name", "slug", "contact_name", "contact_email", "contact_phone", "portal_url")


class IntegratorForm(PrimaryModelForm):
    slug = SlugField()

    fieldsets = (
        FieldSet("name", "slug", "description", "tags", name=_("Company")),
        FieldSet("contact_name", "contact_email", "contact_phone", "portal_url", name=_("Contact")),
    )

    class Meta:
        model = Integrator
        fields = (*COMPANY_FIELDS, "description", "owner", "comments", "tags")


class MaintenanceCompanyForm(IntegratorForm):
    class Meta(IntegratorForm.Meta):
        model = MaintenanceCompany


class IntegratorImportForm(PrimaryModelImportForm):
    slug = SlugField()

    class Meta:
        model = Integrator
        fields = (*COMPANY_FIELDS, "description", "owner", "comments", "tags")


class MaintenanceCompanyImportForm(IntegratorImportForm):
    class Meta(IntegratorImportForm.Meta):
        model = MaintenanceCompany


class IntegratorBulkEditForm(PrimaryModelBulkEditForm):
    model = Integrator
    contact_name = forms.CharField(
        label=pgettext_lazy("support contracts", "Contact name"), max_length=128, required=False,
    )
    contact_email = forms.EmailField(label=pgettext_lazy("support contracts", "Contact email"), required=False)
    contact_phone = forms.CharField(
        label=pgettext_lazy("support contracts", "Contact phone"), max_length=64, required=False,
    )
    portal_url = forms.URLField(label=_("Website / Portal URL"), max_length=500, required=False)

    fieldsets = (FieldSet("contact_name", "contact_email", "contact_phone", "portal_url", "description"),)
    nullable_fields = ("contact_name", "contact_email", "contact_phone", "portal_url", "description", "comments")


class MaintenanceCompanyBulkEditForm(IntegratorBulkEditForm):
    model = MaintenanceCompany
    contact_phone = forms.CharField(label=_("Support phone"), max_length=64, required=False)
    portal_url = forms.URLField(label=_("Support portal URL"), max_length=500, required=False)


class IntegratorFilterForm(PrimaryModelFilterSetForm):
    model = Integrator
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("owner_group_id", "owner_id", name=_("Ownership")),
    )
    tag = TagFilterField(model)


class MaintenanceCompanyFilterForm(IntegratorFilterForm):
    model = MaintenanceCompany
    tag = TagFilterField(model)


#
# Support coverage
#

class SupportCoverageForm(NetBoxModelForm):
    contract = DynamicModelChoiceField(
        label=_("Contract"), queryset=SupportContract.objects.all(), selector=True
    )
    device = DynamicModelChoiceField(label=_("Device"), queryset=Device.objects.all(), required=False, selector=True)
    virtual_machine = DynamicModelChoiceField(
        label=_("Virtual machine"), queryset=VirtualMachine.objects.all(), required=False, selector=True
    )
    module = DynamicModelChoiceField(
        label=_("Module"),
        queryset=Module.objects.all(),
        required=False,
        query_params={"device_id": "$device"},
    )
    integrator = DynamicModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, selector=True,
        quick_add=True,
    )

    fieldsets = (
        FieldSet("contract", name=_("Contract")),
        FieldSet("device", "virtual_machine", "module", name=_("Covered object")),
        FieldSet("sku", "quantity", "start_date", "end_date", "integrator", "notes", "tags", name=_("Coverage")),
    )

    class Meta:
        model = SupportCoverage
        fields = (
            "contract", "device", "virtual_machine", "module", "start_date", "end_date", "integrator", "sku",
            "quantity", "notes", "tags",
        )
        widgets = {"start_date": DatePicker(), "end_date": DatePicker()}


class SupportCoverageImportForm(NetBoxModelImportForm):
    contract = CSVModelChoiceField(
        label=_("Contract"),
        queryset=SupportContract.objects.all(),
        to_field_name="contract_number",
        help_text=_("Contract number (add the manufacturer column when numbers repeat across vendors)"),
    )
    manufacturer = forms.CharField(
        label=_("Manufacturer"), required=False, help_text=_("Contract manufacturer name (optional disambiguation)")
    )
    device = CSVModelChoiceField(
        label=_("Device"), queryset=Device.objects.all(), required=False, to_field_name="name",
        help_text=_("Device name"),
    )
    virtual_machine = CSVModelChoiceField(
        label=_("Virtual machine"), queryset=VirtualMachine.objects.all(), required=False, to_field_name="name",
        help_text=_("Virtual machine name"),
    )
    module = CSVModelChoiceField(
        label=_("Module"), queryset=Module.objects.all(), required=False, to_field_name="serial",
        help_text=_("Module serial number"),
    )
    integrator = CSVModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, to_field_name="name",
        help_text=_("Integrator company name"),
    )

    class Meta:
        model = SupportCoverage
        fields = (
            "contract", "manufacturer", "device", "virtual_machine", "module", "start_date", "end_date",
            "integrator", "sku", "quantity", "notes", "tags",
        )

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(data, *args, **kwargs)
        scope_csv_queryset(self, "contract", "manufacturer__name", "manufacturer")


class SupportCoverageBulkEditForm(NetBoxModelBulkEditForm):
    model = SupportCoverage
    contract = DynamicModelChoiceField(
        label=_("Contract"),
        queryset=SupportContract.objects.all(),
        required=False,
        selector=True,
        help_text=_("Move selected coverage items to another contract (e.g. when switching annual support provider)."),
    )
    start_date = forms.DateField(label=_("Start date override"), required=False, widget=DatePicker())
    end_date = forms.DateField(label=_("End date override"), required=False, widget=DatePicker())
    integrator = DynamicModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, selector=True,
        quick_add=True,
    )
    sku = forms.CharField(label=_("SKU / License Part Number"), max_length=100, required=False)
    quantity = forms.IntegerField(label=_("Quantity"), min_value=0, required=False)
    notes = forms.CharField(label=_("Notes"), max_length=255, required=False)

    fieldsets = (
        FieldSet("contract", name=_("Contract")),
        FieldSet("start_date", "end_date", "integrator", "sku", "quantity", "notes", name=_("Coverage")),
    )
    nullable_fields = ("start_date", "end_date", "integrator", "sku", "quantity", "notes")


class SupportCoverageFilterForm(NetBoxModelFilterSetForm):
    model = SupportCoverage
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("contract_id", "manufacturer_id", "contract_type", "state", name=_("Contract")),
        FieldSet("integrator_id", "maintenance_company_id", name=_("Partners & Providers")),
        FieldSet("object_type", "device_id", "virtual_machine_id", "module_id", "site_id", name=_("Covered object")),
    )
    contract_id = DynamicModelMultipleChoiceField(
        queryset=SupportContract.objects.all(), required=False, label=_("Contract")
    )
    contract_type = forms.MultipleChoiceField(
        choices=ContractTypeChoices, required=False, label=_("Contract type")
    )
    integrator_id = DynamicModelMultipleChoiceField(
        queryset=Integrator.objects.all(), required=False, label=_("Integrator company")
    )
    maintenance_company_id = DynamicModelMultipleChoiceField(
        queryset=MaintenanceCompany.objects.all(), required=False, label=_("Maintenance company")
    )
    manufacturer_id = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label=_("Manufacturer")
    )
    state = forms.MultipleChoiceField(choices=ContractStateChoices, required=False, label=STATE)
    object_type = forms.ChoiceField(
        choices=(
            ("", "---------"),
            ("device", _("Device")),
            ("virtual_machine", _("Virtual machine")),
            ("module", _("Module")),
            ("none", _("Contract-level (SKU only)")),
        ),
        required=False,
        label=_("Object type"),
    )
    device_id = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False, label=_("Device"))
    virtual_machine_id = DynamicModelMultipleChoiceField(
        queryset=VirtualMachine.objects.all(), required=False, label=_("Virtual machine")
    )
    module_id = DynamicModelMultipleChoiceField(queryset=Module.objects.all(), required=False, label=_("Module"))
    site_id = DynamicModelMultipleChoiceField(queryset=Site.objects.all(), required=False, label=_("Site"))
    tag = TagFilterField(model)


#
# Hardware lifecycle (EoL)
#

class HardwareLifecycleEoLForm(PrimaryModelForm):
    device_type = DynamicModelChoiceField(
        label=_("Device type"), queryset=DeviceType.objects.all(), required=False, selector=True
    )
    module_type = DynamicModelChoiceField(
        label=_("Module type"), queryset=ModuleType.objects.all(), required=False, selector=True
    )

    fieldsets = (
        FieldSet("device_type", "module_type", "description", "tags", name=_("Hardware")),
        FieldSet("end_of_sale", "end_of_maintenance", "end_of_support", "notice_url", name=LIFECYCLE),
    )

    class Meta:
        model = HardwareLifecycleEoL
        fields = (
            "device_type", "module_type", "end_of_sale", "end_of_maintenance", "end_of_support", "notice_url",
            "description", "owner", "comments", "tags",
        )
        widgets = {
            "end_of_sale": DatePicker(),
            "end_of_maintenance": DatePicker(),
            "end_of_support": DatePicker(),
        }


class HardwareLifecycleEoLImportForm(PrimaryModelImportForm):
    manufacturer = forms.CharField(
        label=_("Manufacturer"), required=False, help_text=_("Manufacturer name (disambiguates the type model)")
    )
    device_type = CSVModelChoiceField(
        label=_("Device type"), queryset=DeviceType.objects.all(), required=False, to_field_name="model",
        help_text=_("Device type model name"),
    )
    module_type = CSVModelChoiceField(
        label=_("Module type"), queryset=ModuleType.objects.all(), required=False, to_field_name="model",
        help_text=_("Module type model name"),
    )

    class Meta:
        model = HardwareLifecycleEoL
        fields = (
            "manufacturer", "device_type", "module_type", "end_of_sale", "end_of_maintenance", "end_of_support",
            "notice_url", "description", "owner", "comments", "tags",
        )

    def __init__(self, data=None, *args, **kwargs):
        super().__init__(data, *args, **kwargs)
        scope_csv_queryset(self, "device_type", "manufacturer__name", "manufacturer")
        scope_csv_queryset(self, "module_type", "manufacturer__name", "manufacturer")


class HardwareLifecycleEoLBulkEditForm(PrimaryModelBulkEditForm):
    model = HardwareLifecycleEoL
    end_of_sale = forms.DateField(label=_("End of sale"), required=False, widget=DatePicker())
    end_of_maintenance = forms.DateField(label=_("End of maintenance"), required=False, widget=DatePicker())
    end_of_support = forms.DateField(label=_("End of support"), required=False, widget=DatePicker())
    notice_url = forms.URLField(label=_("Notice URL"), max_length=500, required=False)

    fieldsets = (FieldSet("end_of_sale", "end_of_maintenance", "end_of_support", "notice_url", "description"),)
    nullable_fields = ("end_of_sale", "end_of_maintenance", "end_of_support", "notice_url", "description", "comments")


class HardwareLifecycleEoLFilterForm(PrimaryModelFilterSetForm):
    model = HardwareLifecycleEoL
    fieldsets = (
        FieldSet("q", "filter_id", "tag"),
        FieldSet("manufacturer_id", "device_type_id", "module_type_id", "state", name=_("Hardware")),
        FieldSet("owner_group_id", "owner_id", name=_("Ownership")),
    )
    manufacturer_id = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label=_("Manufacturer")
    )
    device_type_id = DynamicModelMultipleChoiceField(
        queryset=DeviceType.objects.all(), required=False, query_params={"manufacturer_id": "$manufacturer_id"},
        label=_("Device type"),
    )
    module_type_id = DynamicModelMultipleChoiceField(
        queryset=ModuleType.objects.all(), required=False, query_params={"manufacturer_id": "$manufacturer_id"},
        label=_("Module type"),
    )
    state = forms.MultipleChoiceField(choices=ContractStateChoices, required=False, label=_("Support state"))
    tag = TagFilterField(model)


#
# Device coverage report
#

class DeviceCoverageFilterForm(forms.Form):
    fieldsets = (
        FieldSet("q", "coverage"),
        FieldSet(
            "expiring_within_days", "coverage_end_after", "coverage_end_before", name=_("Coverage end window")
        ),
        FieldSet("contract_id", "contract_type", "integrator_id", "maintenance_company_id", name=_("Contract")),
        FieldSet("status", "manufacturer_id", "device_type_id", "site_id", "role_id", name=_("Device")),
    )
    q = forms.CharField(required=False, label=_("Search"))
    expiring_within_days = forms.IntegerField(min_value=0, required=False, label=_("Coverage ends within (days)"))
    coverage_end_after = forms.DateField(required=False, widget=DatePicker(), label=_("Coverage ends on or after"))
    coverage_end_before = forms.DateField(
        required=False, widget=DatePicker(), label=_("Coverage ends on or before")
    )
    contract_id = DynamicModelMultipleChoiceField(
        queryset=SupportContract.objects.all(), required=False, label=_("Contract")
    )
    status = forms.MultipleChoiceField(choices=DeviceStatusChoices, required=False, label=_("Device status"))
    contract_type = forms.MultipleChoiceField(
        choices=ContractTypeChoices, required=False, label=_("Contract type")
    )
    integrator_id = DynamicModelMultipleChoiceField(
        queryset=Integrator.objects.all(), required=False, label=_("Integrator company")
    )
    maintenance_company_id = DynamicModelMultipleChoiceField(
        queryset=MaintenanceCompany.objects.all(), required=False, label=_("Maintenance company")
    )
    coverage = forms.MultipleChoiceField(
        choices=(
            ("covered", _("Covered")),
            ("expiring", _("Expiring")),
            ("expired", _("Expired")),
            ("none", _("Not covered")),
        ),
        required=False,
        label=_("Coverage"),
    )
    manufacturer_id = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label=_("Manufacturer")
    )
    device_type_id = DynamicModelMultipleChoiceField(
        queryset=DeviceType.objects.all(), required=False, label=_("Device type")
    )
    site_id = DynamicModelMultipleChoiceField(queryset=Site.objects.all(), required=False, label=_("Site"))
    role_id = DynamicModelMultipleChoiceField(queryset=DeviceRole.objects.all(), required=False, label=_("Role"))


#
# Bulk coverage (Danbyte-style device picker + serial matcher)
#

class BulkCoverageAddForm(forms.Form):
    devices = DynamicModelMultipleChoiceField(
        label=DEVICES, queryset=Device.objects.all(), required=False, selector=True
    )
    virtual_machines = DynamicModelMultipleChoiceField(
        label=_("Virtual machines"), queryset=VirtualMachine.objects.all(), required=False, selector=True
    )
    modules = DynamicModelMultipleChoiceField(label=_("Modules"), queryset=Module.objects.all(), required=False)
    site = DynamicModelMultipleChoiceField(
        label=_("Site"), queryset=Site.objects.all(), required=False,
        help_text=_("Cover every device matching all of the selected site / device type / role filters"),
    )
    device_type = DynamicModelMultipleChoiceField(
        label=_("Device type"), queryset=DeviceType.objects.all(), required=False
    )
    role = DynamicModelMultipleChoiceField(label=_("Role"), queryset=DeviceRole.objects.all(), required=False)
    serials = forms.CharField(
        label=_("Serial numbers"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 10, "class": "font-monospace", "placeholder": "SN-10001\nSN-10002"}),
        help_text=format_lazy(
            _(
                "Paste up to {max} serial numbers separated by newlines, commas, semicolons, or spaces "
                "(e.g. straight from a spreadsheet column, invoice, or vendor quote). Matching is case-insensitive."
            ),
            max=MAX_BULK_ITEMS,
        ),
    )
    sku = forms.CharField(label=_("SKU / License Part Number"), max_length=100, required=False)
    quantity = forms.IntegerField(
        label=_("Quantity"), min_value=0, required=False,
        help_text=_("License count, seats, sockets, or covered units (optional)."),
    )
    start_date = forms.DateField(
        label=_("Start date override"), required=False, widget=DatePicker(),
        help_text=_("Leave blank to follow the contract start date"),
    )
    end_date = forms.DateField(
        label=_("End date override"), required=False, widget=DatePicker(),
        help_text=_("Leave blank to follow the contract end date"),
    )
    integrator = DynamicModelChoiceField(
        label=_("Integrator company"), queryset=Integrator.objects.all(), required=False, selector=True,
        quick_add=True, help_text=_("Leave blank to follow the contract integrator"),
    )
    notes = forms.CharField(label=_("Notes"), max_length=255, required=False)

    fieldsets = (
        FieldSet("devices", "virtual_machines", "modules", name=_("Option A: Pick existing objects")),
        FieldSet("site", "device_type", "role", name=_("…or cover all matching devices")),
        FieldSet("serials", name=_("Option B: Paste serial numbers")),
        FieldSet(
            "sku", "quantity", "start_date", "end_date", "integrator", "notes",
            name=_("Applied to each new coverage row"),
        ),
    )

    FILTER_FIELDS = ("site", "device_type", "role")

    def clean_serials(self):
        serials = parse_serials(self.cleaned_data.get("serials"))
        if len(serials) > MAX_BULK_ITEMS:
            raise forms.ValidationError(
                _("At most {max} serial numbers can be processed at once ({count} given).").format(
                    max=MAX_BULK_ITEMS, count=len(serials)
                )
            )
        return serials

    def clean(self):
        cleaned = super().clean()
        picked = any(cleaned.get(name) for name in ("devices", "virtual_machines", "modules", "serials"))
        if not picked and not self.has_device_filters:
            raise forms.ValidationError(
                _("Pick at least one object, choose a site / device type / role filter, or paste serial numbers.")
            )
        return cleaned

    @property
    def has_device_filters(self):
        return any(self.cleaned_data.get(name) for name in self.FILTER_FIELDS)

    def filtered_devices(self, queryset):
        """Apply the site / device type / role filters (ANDed) to a device queryset."""
        lookups = {"site": "site__in", "device_type": "device_type__in", "role": "role__in"}
        for name, lookup in lookups.items():
            if values := self.cleaned_data.get(name):
                queryset = queryset.filter(**{lookup: values})
        return queryset
