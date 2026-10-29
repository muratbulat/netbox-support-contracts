"""
Test suite for netbox-support-contracts. Runs inside NetBox against a throwaway test database; from NetBox's
netbox/ directory, with the plugin installed and enabled:

    python manage.py test --keepdb /path/to/netbox-support-contracts/tests

Fixture helpers below use placeholder data only (Acme Corp, Device-01, SN-10001, CONT-2026-001).
"""

from datetime import timedelta

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Module, ModuleBay, ModuleType, Site
from django.utils import timezone
from virtualization.models import Cluster, ClusterType, VirtualMachine

from netbox_support_contracts.models import Integrator, MaintenanceCompany, SupportContract, SupportCoverage


def today():
    return timezone.localdate()


def days(n):
    """A date n days from today (negative = past)."""
    return today() + timedelta(days=n)


def make_manufacturer(name="Acme Corp"):
    slug = name.lower().replace(" ", "-")
    return Manufacturer.objects.get_or_create(name=name, defaults={"slug": slug})[0]


def make_device_type(model="Switch-48", manufacturer=None):
    manufacturer = manufacturer or make_manufacturer()
    return DeviceType.objects.get_or_create(
        manufacturer=manufacturer, model=model, defaults={"slug": model.lower()}
    )[0]


def make_device(name="Device-01", serial="", device_type=None, site=None):
    site = site or Site.objects.get_or_create(name="Site-01", defaults={"slug": "site-01"})[0]
    role = DeviceRole.objects.get_or_create(name="Access", defaults={"slug": "access"})[0]
    return Device.objects.create(
        name=name, serial=serial, site=site, role=role, device_type=device_type or make_device_type()
    )


def make_vm(name="VM-01"):
    cluster_type = ClusterType.objects.get_or_create(name="Hypervisor", defaults={"slug": "hypervisor"})[0]
    cluster = Cluster.objects.get_or_create(name="Cluster-01", defaults={"type": cluster_type})[0]
    return VirtualMachine.objects.create(name=name, cluster=cluster)


def make_module(device, serial="MOD-10001", model="Linecard-8"):
    module_type = ModuleType.objects.get_or_create(manufacturer=make_manufacturer(), model=model)[0]
    bay = ModuleBay.objects.create(device=device, name=f"Bay-{ModuleBay.objects.count() + 1}")
    return Module.objects.create(device=device, module_bay=bay, module_type=module_type, serial=serial)


def make_integrator(name="Example Integrator"):
    slug = name.lower().replace(" ", "-")
    return Integrator.objects.get_or_create(name=name, defaults={"slug": slug})[0]


def make_maintenance_company(name="Example Maintenance"):
    slug = name.lower().replace(" ", "-")
    return MaintenanceCompany.objects.get_or_create(name=name, defaults={"slug": slug})[0]


DEFAULT = object()


def make_contract(number="CONT-2026-001", end_date=None, manufacturer=DEFAULT, **kwargs):
    """A contract for Acme Corp unless another manufacturer (or None, e.g. for TPM contracts) is given."""
    if manufacturer is DEFAULT:
        manufacturer = make_manufacturer()
    return SupportContract.objects.create(
        manufacturer=manufacturer, contract_number=number, end_date=end_date, **kwargs
    )


def cover(contract, obj=None, **kwargs):
    field = {"device": "device", "virtualmachine": "virtual_machine", "module": "module"}[obj._meta.model_name]
    return SupportCoverage.objects.create(contract=contract, **{field: obj}, **kwargs)
