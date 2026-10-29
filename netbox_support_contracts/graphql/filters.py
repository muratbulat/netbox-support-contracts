from typing import TYPE_CHECKING, Annotated

import strawberry
import strawberry_django
from netbox.graphql.filters import NetBoxModelFilter, PrimaryModelFilter
from strawberry.scalars import ID
from strawberry_django import DateFilterLookup, StrFilterLookup

from .. import models

if TYPE_CHECKING:
    from dcim.graphql.filters import (
        DeviceFilter,
        DeviceTypeFilter,
        ManufacturerFilter,
        ModuleFilter,
        ModuleTypeFilter,
    )
    from netbox.graphql.filter_lookups import IntegerLookup
    from tenancy.graphql.filters import TenantFilter
    from virtualization.graphql.filters import VirtualMachineFilter

# Lazy references to this module (types/filters that refer to each other).
PLUGIN_FILTERS = "netbox_support_contracts.graphql.filters"

__all__ = (
    "HardwareLifecycleEoLFilter",
    "IntegratorFilter",
    "MaintenanceCompanyFilter",
    "SupportContractFilter",
    "SupportCoverageFilter",
)


@strawberry_django.filter_type(models.Integrator, lookups=True)
class IntegratorFilter(PrimaryModelFilter):
    name: StrFilterLookup | None = strawberry_django.filter_field()
    slug: StrFilterLookup | None = strawberry_django.filter_field()
    contact_name: StrFilterLookup | None = strawberry_django.filter_field()
    contact_email: StrFilterLookup | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.MaintenanceCompany, lookups=True)
class MaintenanceCompanyFilter(PrimaryModelFilter):
    name: StrFilterLookup | None = strawberry_django.filter_field()
    slug: StrFilterLookup | None = strawberry_django.filter_field()
    contact_name: StrFilterLookup | None = strawberry_django.filter_field()
    contact_email: StrFilterLookup | None = strawberry_django.filter_field()


# Cost/currency are deliberately not filterable: filtering would leak values gated by view_cost.
@strawberry_django.filter_type(models.SupportContract, lookups=True)
class SupportContractFilter(PrimaryModelFilter):
    manufacturer: Annotated["ManufacturerFilter", strawberry.lazy("dcim.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    manufacturer_id: ID | None = strawberry_django.filter_field()
    contract_number: StrFilterLookup | None = strawberry_django.filter_field()
    contract_type: StrFilterLookup | None = strawberry_django.filter_field()
    service_level: StrFilterLookup | None = strawberry_django.filter_field()
    start_date: DateFilterLookup | None = strawberry_django.filter_field()
    end_date: DateFilterLookup | None = strawberry_django.filter_field()
    integrator: Annotated["IntegratorFilter", strawberry.lazy(PLUGIN_FILTERS)] | None = (
        strawberry_django.filter_field()
    )
    integrator_id: ID | None = strawberry_django.filter_field()
    maintenance_company: Annotated["MaintenanceCompanyFilter", strawberry.lazy(PLUGIN_FILTERS)] | None = (
        strawberry_django.filter_field()
    )
    maintenance_company_id: ID | None = strawberry_django.filter_field()
    tenant: Annotated["TenantFilter", strawberry.lazy("tenancy.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    tenant_id: ID | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.SupportCoverage, lookups=True)
class SupportCoverageFilter(NetBoxModelFilter):
    contract: Annotated["SupportContractFilter", strawberry.lazy(PLUGIN_FILTERS)] | None = (
        strawberry_django.filter_field()
    )
    contract_id: ID | None = strawberry_django.filter_field()
    device: Annotated["DeviceFilter", strawberry.lazy("dcim.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    device_id: ID | None = strawberry_django.filter_field()
    virtual_machine: Annotated["VirtualMachineFilter", strawberry.lazy("virtualization.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    virtual_machine_id: ID | None = strawberry_django.filter_field()
    module: Annotated["ModuleFilter", strawberry.lazy("dcim.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    module_id: ID | None = strawberry_django.filter_field()
    end_date: DateFilterLookup | None = strawberry_django.filter_field()
    sku: StrFilterLookup | None = strawberry_django.filter_field()
    quantity: Annotated["IntegerLookup", strawberry.lazy("netbox.graphql.filter_lookups")] | None = (
        strawberry_django.filter_field()
    )
    notes: StrFilterLookup | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.HardwareLifecycleEoL, lookups=True)
class HardwareLifecycleEoLFilter(PrimaryModelFilter):
    device_type: Annotated["DeviceTypeFilter", strawberry.lazy("dcim.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    device_type_id: ID | None = strawberry_django.filter_field()
    module_type: Annotated["ModuleTypeFilter", strawberry.lazy("dcim.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    module_type_id: ID | None = strawberry_django.filter_field()
    end_of_sale: DateFilterLookup | None = strawberry_django.filter_field()
    end_of_maintenance: DateFilterLookup | None = strawberry_django.filter_field()
    end_of_support: DateFilterLookup | None = strawberry_django.filter_field()
