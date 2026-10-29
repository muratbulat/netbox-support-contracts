from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

import strawberry
import strawberry_django
from netbox.graphql.types import NetBoxObjectType, PrimaryObjectType
from strawberry.types import Info

from .. import models
from ..services import may_view_cost
from .filters import (
    HardwareLifecycleEoLFilter,
    IntegratorFilter,
    MaintenanceCompanyFilter,
    SupportContractFilter,
    SupportCoverageFilter,
)

if TYPE_CHECKING:
    from dcim.graphql.types import DeviceType, DeviceTypeType, ManufacturerType, ModuleType, ModuleTypeType
    from tenancy.graphql.types import TenantType
    from virtualization.graphql.types import VirtualMachineType

# Lazy references to this module (types/filters that refer to each other).
PLUGIN_TYPES = "netbox_support_contracts.graphql.types"

__all__ = (
    "HardwareLifecycleEoLType",
    "IntegratorType",
    "MaintenanceCompanyType",
    "SupportContractType",
    "SupportCoverageType",
)


@strawberry_django.type(models.Integrator, fields="__all__", filters=IntegratorFilter, pagination=True)
class IntegratorType(PrimaryObjectType):
    support_contracts: list[
        Annotated["SupportContractType", strawberry.lazy(PLUGIN_TYPES)]
    ]


@strawberry_django.type(
    models.MaintenanceCompany, fields="__all__", filters=MaintenanceCompanyFilter, pagination=True
)
class MaintenanceCompanyType(PrimaryObjectType):
    support_contracts: list[
        Annotated["SupportContractType", strawberry.lazy(PLUGIN_TYPES)]
    ]


@strawberry_django.type(
    models.SupportContract,
    exclude=["cost", "currency", "notified_state"],
    filters=SupportContractFilter,
    pagination=True,
)
class SupportContractType(PrimaryObjectType):
    manufacturer: Annotated["ManufacturerType", strawberry.lazy("dcim.graphql.types")] | None
    integrator: Annotated["IntegratorType", strawberry.lazy(PLUGIN_TYPES)] | None
    maintenance_company: Annotated["MaintenanceCompanyType", strawberry.lazy(PLUGIN_TYPES)] | None
    tenant: Annotated["TenantType", strawberry.lazy("tenancy.graphql.types")] | None
    coverage: list[Annotated["SupportCoverageType", strawberry.lazy(PLUGIN_TYPES)]]

    @strawberry_django.field
    def state(self) -> str:
        return self.state

    @strawberry_django.field
    def days_remaining(self) -> int | None:
        return self.days_remaining

    # Commercial fields resolve to null for users without netbox_support_contracts.view_cost.
    @strawberry_django.field(only=["cost"])
    def cost(self, info: Info) -> Decimal | None:
        return self.cost if may_view_cost(info.context.request.user) else None

    @strawberry_django.field(only=["currency"])
    def currency(self, info: Info) -> str | None:
        return self.currency if may_view_cost(info.context.request.user) else None


@strawberry_django.type(
    models.SupportCoverage,
    fields="__all__",
    filters=SupportCoverageFilter,
    pagination=True,
)
class SupportCoverageType(NetBoxObjectType):
    contract: Annotated["SupportContractType", strawberry.lazy(PLUGIN_TYPES)]
    device: Annotated["DeviceType", strawberry.lazy("dcim.graphql.types")] | None
    virtual_machine: Annotated["VirtualMachineType", strawberry.lazy("virtualization.graphql.types")] | None
    module: Annotated["ModuleType", strawberry.lazy("dcim.graphql.types")] | None
    integrator: Annotated["IntegratorType", strawberry.lazy(PLUGIN_TYPES)] | None

    @strawberry_django.field(only=["end_date", "contract__end_date"])
    def effective_end_date(self) -> date | None:
        return self.effective_end_date

    @strawberry_django.field(only=["end_date", "contract__end_date"])
    def state(self) -> str:
        return self.state

    @strawberry_django.field(only=["end_date", "contract__end_date"])
    def days_remaining(self) -> int | None:
        return self.days_remaining


@strawberry_django.type(
    models.HardwareLifecycleEoL,
    fields="__all__",
    filters=HardwareLifecycleEoLFilter,
    pagination=True,
)
class HardwareLifecycleEoLType(PrimaryObjectType):
    device_type: Annotated["DeviceTypeType", strawberry.lazy("dcim.graphql.types")] | None
    module_type: Annotated["ModuleTypeType", strawberry.lazy("dcim.graphql.types")] | None

    @strawberry_django.field(only=["end_of_support"])
    def state(self) -> str:
        return self.state

    @strawberry_django.field(only=["end_of_support"])
    def days_remaining(self) -> int | None:
        return self.days_remaining
