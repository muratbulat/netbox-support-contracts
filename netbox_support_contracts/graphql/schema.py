import strawberry
import strawberry_django

from .types import HardwareLifecycleEoLType, SupportContractType, SupportCoverageType


@strawberry.type(name="Query")
class SupportContractsQuery:
    support_contract: SupportContractType = strawberry_django.field()
    support_contract_list: list[SupportContractType] = strawberry_django.field()

    support_coverage: SupportCoverageType = strawberry_django.field()
    support_coverage_list: list[SupportCoverageType] = strawberry_django.field()

    hardware_lifecycle_eol: HardwareLifecycleEoLType = strawberry_django.field()
    hardware_lifecycle_eol_list: list[HardwareLifecycleEoLType] = strawberry_django.field()


schema = [SupportContractsQuery]
