from netbox.search import SearchIndex, register_search

from .models import HardwareLifecycleEoL, SupportContract, SupportCoverage


@register_search
class SupportContractIndex(SearchIndex):
    model = SupportContract
    fields = (
        ("contract_number", 100),
        ("reseller", 300),
        ("service_level", 500),
        ("description", 500),
        ("comments", 5000),
    )
    display_attrs = ("manufacturer", "service_level", "end_date", "tenant", "description")


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
