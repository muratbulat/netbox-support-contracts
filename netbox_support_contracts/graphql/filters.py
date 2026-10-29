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
    from tenancy.graphql.filters import TenantFilter
    from virtualization.graphql.filters import VirtualMachineFilter

__all__ = ("HardwareLifecycleEoLFilter", "SupportContractFilter", "SupportCoverageFilter")


# Cost/currency are deliberately not filterable: filtering would leak values gated by view_cost.
@strawberry_django.filter_type(models.SupportContract, lookups=True)
class SupportContractFilter(PrimaryModelFilter):
    manufacturer: Annotated["ManufacturerFilter", strawberry.lazy("dcim.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    manufacturer_id: ID | None = strawberry_django.filter_field()
    contract_number: StrFilterLookup | None = strawberry_django.filter_field()
    service_level: StrFilterLookup | None = strawberry_django.filter_field()
    start_date: DateFilterLookup | None = strawberry_django.filter_field()
    end_date: DateFilterLookup | None = strawberry_django.filter_field()
    reseller: StrFilterLookup | None = strawberry_django.filter_field()
    tenant: Annotated["TenantFilter", strawberry.lazy("tenancy.graphql.filters")] | None = (
        strawberry_django.filter_field()
    )
    tenant_id: ID | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.SupportCoverage, lookups=True)
class SupportCoverageFilter(NetBoxModelFilter):
    contract: Annotated["SupportContractFilter", strawberry.lazy("netbox_support_contracts.graphql.filters")] | None = (
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
