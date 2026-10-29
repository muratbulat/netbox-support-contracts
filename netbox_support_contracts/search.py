from netbox.search import SearchIndex, register_search

from .models import HardwareLifecycleEoL, Integrator, MaintenanceCompany, SupportContract, SupportCoverage


@register_search
class SupportContractIndex(SearchIndex):
    model = SupportContract
    fields = (
        ("contract_number", 100),
        ("service_level", 500),
        ("description", 500),
        ("comments", 5000),
    )
    display_attrs = (
        "manufacturer", "integrator", "maintenance_company", "service_level", "end_date", "tenant", "description",
    )


@register_search
class SupportCoverageIndex(SearchIndex):
    model = SupportCoverage
    fields = (
        ("sku", 200),
        ("notes", 2000),
    )
    display_attrs = ("contract", "device", "virtual_machine", "module", "end_date")


@register_search
class HardwareLifecycleEoLIndex(SearchIndex):
    model = HardwareLifecycleEoL
    fields = (
        ("description", 500),
        ("comments", 5000),
    )
    display_attrs = ("device_type", "module_type", "end_of_sale", "end_of_support")


@register_search
class IntegratorIndex(SearchIndex):
    model = Integrator
    fields = (
        ("name", 100),
        ("slug", 110),
        ("contact_name", 300),
        ("contact_email", 300),
        ("description", 500),
        ("comments", 5000),
    )
    display_attrs = ("contact_name", "contact_email", "contact_phone", "description")


@register_search
class MaintenanceCompanyIndex(SearchIndex):
    model = MaintenanceCompany
    fields = IntegratorIndex.fields
    display_attrs = IntegratorIndex.display_attrs
