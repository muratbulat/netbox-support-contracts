import csv
import io

from dcim.models import Device, ModuleType
from dcim.tables import ModuleTable, ModuleTypeTable
from django.conf import settings
from django.test import RequestFactory, override_settings
from django.urls import reverse
from netbox.context_managers import event_tracking
from users.models import ObjectPermission, User
from utilities.testing import TestCase
from virtualization.models import VirtualMachine
from virtualization.tables import VirtualMachineTable

from netbox_support_contracts.choices import ContractTypeChoices
from netbox_support_contracts.filtersets import DeviceCoverageFilterSet
from netbox_support_contracts.models import HardwareLifecycleEoL

from . import (
    cover,
    days,
    make_contract,
    make_device,
    make_device_type,
    make_integrator,
    make_maintenance_company,
    make_manufacturer,
    make_module,
    make_vm,
)

PLUGIN = "netbox_support_contracts"
EN_HEADERS = [
    "Manufacturer", "Device Type", "Part Number", "Model Device Count", "Device", "Serial Number", "Asset Tag", "Site",
    "Rack", "Role", "Device Status", "Coverage Status", "Current Contract", "Current Contract Type",
    "Integrator Company", "Maintenance Company", "Current Coverage End Date", "Days Remaining",
    "Hardware End of Support (EoS)",
]


def url(name, *args):
    return reverse(f"plugins:{PLUGIN}:{name}", args=args)


def parse_csv(response):
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"].startswith('attachment; filename="support-provider-devices-')
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    return rows[0], [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]


class ExportFixtures:
    @classmethod
    def setUpTestData(cls):
        vendor = make_manufacturer("Acme Corp")
        cls.model_a = make_device_type("Storage-A", manufacturer=vendor)
        cls.model_a.part_number = "PN-STOR-A"
        cls.model_a.save()
        cls.model_b = make_device_type("Server-B", manufacturer=vendor)
        HardwareLifecycleEoL.objects.create(device_type=cls.model_a, end_of_support=days(180))
        cls.stor_1 = make_device("Storage-01", serial="SN-10001", device_type=cls.model_a)
        cls.stor_2 = make_device("Storage-02", serial="SN-10002", device_type=cls.model_a)
        cls.stor_3 = make_device("Storage-03", serial="SN-10003", device_type=cls.model_a)
        cls.server = make_device("Server-01", serial="SN-20001", device_type=cls.model_b)
        cls.spare = make_device("Server-02", serial="SN-20002", device_type=cls.model_b)
        Device.objects.filter(pk=cls.stor_3.pk).update(status="offline")
        Device.objects.filter(pk=cls.stor_1.pk).update(asset_tag="AT-0001")
        cls.warranty = make_contract(
            "CONT-2026-001", end_date=days(60), contract_type=ContractTypeChoices.WARRANTY,
            integrator=make_integrator("Example Distributor"),
        )
        cls.tpm = make_contract(
            "CONT-2026-002", end_date=days(400), contract_type=ContractTypeChoices.TPM,
            maintenance_company=make_maintenance_company("Example Maintenance A"),
            manufacturer=make_manufacturer("Example TPM"),
        )
        cls.open = make_contract("CONT-2026-003", end_date=None, integrator=make_integrator("Example Partner"))
        for device in (cls.stor_1, cls.stor_2, cls.stor_3):
            cover(cls.warranty, device)
        cover(cls.tpm, cls.server)
        cover(cls.open, cls.spare)


class DeviceCoverageFilterTests(ExportFixtures, TestCase):
    def names(self, **params):
        return sorted(DeviceCoverageFilterSet(params, Device.objects.all()).qs.values_list("name", flat=True))

    def test_status(self):
        self.assertNotIn("Storage-03", self.names(status=["active"]))
        self.assertEqual(self.names(status=["offline"]), ["Storage-03"])

    def test_expiring_within_days(self):
        self.assertEqual(self.names(expiring_within_days=90), ["Storage-01", "Storage-02", "Storage-03"])
        self.assertEqual(self.names(expiring_within_days=59), [])
        everything_dated = ["Server-01", "Storage-01", "Storage-02", "Storage-03"]
        self.assertEqual(self.names(expiring_within_days=10**9), everything_dated)

    def test_coverage_end_window_excludes_open_ended(self):
        storage = ["Storage-01", "Storage-02", "Storage-03"]
        self.assertEqual(self.names(coverage_end_before=days(60).isoformat()), storage)
        self.assertEqual(self.names(coverage_end_after=days(61).isoformat()), ["Server-01"])
        self.assertEqual(
            self.names(coverage_end_after=days(0).isoformat(), coverage_end_before=days(365).isoformat()),
            ["Storage-01", "Storage-02", "Storage-03"],
        )

    def test_transition_candidates(self):
        # Active devices whose OEM warranty ends within 90 days: the next takeover batch for a TPM provider.
        names = self.names(status=["active"], contract_type=["warranty"], expiring_within_days=90)
        self.assertEqual(names, ["Storage-01", "Storage-02"])

    def test_contract_partner_companies_and_type(self):
        self.assertEqual(self.names(contract_id=[self.tpm.pk]), ["Server-01"])
        self.assertEqual(self.names(maintenance_company_id=[self.tpm.maintenance_company.pk]), ["Server-01"])
        self.assertEqual(
            self.names(integrator_id=[self.warranty.integrator.pk]), ["Storage-01", "Storage-02", "Storage-03"]
        )
        self.assertEqual(self.names(contract_type=["warranty"]), ["Storage-01", "Storage-02", "Storage-03"])


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class ProviderExportTests(ExportFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.add_permissions(
            "dcim.view_device", f"{PLUGIN}.view_supportcoverage", f"{PLUGIN}.view_supportcontract",
            f"{PLUGIN}.view_hardwarelifecycleeol",
        )

    def export(self, **params):
        return self.client.get(url("device_coverage"), {"export": "provider_csv", **params})

    def test_device_coverage_export(self):
        headers, rows = parse_csv(self.export(status="active", expiring_within_days=90))
        self.assertEqual(headers, EN_HEADERS)
        self.assertEqual([row["Serial Number"] for row in rows], ["SN-10001", "SN-10002"])
        first = rows[0]
        self.assertEqual(first["Manufacturer"], "Acme Corp")
        self.assertEqual(first["Device Type"], "Storage-A")
        self.assertEqual(first["Part Number"], "PN-STOR-A")
        self.assertEqual(first["Model Device Count"], "2")  # units of this model in the exported set
        self.assertEqual(first["Asset Tag"], "AT-0001")
        self.assertEqual(first["Site"], "Site-01")
        self.assertEqual(first["Device Status"], "Active")
        self.assertEqual(first["Coverage Status"], "Expiring")
        self.assertEqual(first["Current Contract"], "CONT-2026-001")
        self.assertEqual(first["Current Contract Type"], "OEM Warranty")
        self.assertEqual(first["Integrator Company"], "Example Distributor")
        self.assertEqual(first["Maintenance Company"], "")
        self.assertEqual(first["Current Coverage End Date"], days(60).isoformat())
        self.assertEqual(first["Days Remaining"], "60")
        self.assertEqual(first["Hardware End of Support (EoS)"], days(180).isoformat())

    def test_full_export_counts_open_ended_and_uncovered(self):
        uncovered = make_device("Server-03", serial="SN-20003", device_type=self.model_b)
        _headers, rows = parse_csv(self.export())
        by_serial = {row["Serial Number"]: row for row in rows}
        self.assertEqual(len(rows), 6)
        self.assertEqual(by_serial["SN-20002"]["Current Coverage End Date"], "Open-ended")
        self.assertEqual(by_serial["SN-20002"]["Days Remaining"], "")
        self.assertEqual(by_serial[uncovered.serial]["Coverage Status"], "Not covered")
        self.assertEqual(by_serial[uncovered.serial]["Current Contract"], "")
        self.assertEqual(by_serial["SN-20001"]["Model Device Count"], "3")
        self.assertEqual(by_serial["SN-10001"]["Model Device Count"], "3")

    def test_turkish_export(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "tr"
        headers, rows = parse_csv(self.export(serial=""))
        self.assertEqual(headers[:4], ["Üretici", "Cihaz Türü / Model", "Parça Numarası", "Model Toplam Cihaz Adedi"])
        self.assertIn("Lokasyon / Saha", headers)
        self.assertIn("Kabinet", headers)
        self.assertIn("Donanım Destek Sonu (EoS)", headers)
        by_serial = {row["Seri Numarası"]: row for row in rows}
        self.assertEqual(by_serial["SN-10001"]["Kapsam Durumu"], "Süresi Yakında Dolacak")
        self.assertEqual(by_serial["SN-10001"]["Mevcut Sözleşme Türü"], "Üretici Garantisi (OEM)")
        self.assertEqual(by_serial["SN-20002"]["Mevcut Kapsam Bitiş Tarihi"], "Süresiz")

    def test_csv_delimiter_preference(self):
        self.user.config.set("csv_delimiter", "comma", commit=True)
        headers, _rows = parse_csv(self.export(expiring_within_days=90))
        self.assertEqual(headers, EN_HEADERS)
        self.user.config.set("csv_delimiter", "semicolon", commit=True)
        response = self.export(expiring_within_days=90)
        first_line = response.content.decode("utf-8-sig").splitlines()[0]
        self.assertEqual(first_line.split(";"), EN_HEADERS)

    def test_respects_device_permissions(self):
        self.remove_permissions("dcim.view_device")
        permission = ObjectPermission.objects.create(name="sites", actions=["view"], constraints={"name": "Storage-01"})
        from core.models import ObjectType

        permission.object_types.add(ObjectType.objects.get_for_model(Device))
        permission.users.add(self.user)
        _headers, rows = parse_csv(self.export())
        self.assertEqual([row["Serial Number"] for row in rows], ["SN-10001"])

    def test_lifecycle_and_coverage_hidden_without_permission(self):
        self.remove_permissions(f"{PLUGIN}.view_hardwarelifecycleeol")
        _headers, rows = parse_csv(self.export(expiring_within_days=90))
        self.assertEqual({row["Hardware End of Support (EoS)"] for row in rows}, {""})
        self.remove_permissions(f"{PLUGIN}.view_supportcoverage")
        self.assertEqual(self.export().status_code, 403)

    def test_contract_coverage_tab_export(self):
        response = self.client.get(url("supportcontract_coverage", self.warranty.pk), {"export": "provider_csv"})
        headers, rows = parse_csv(response)
        self.assertEqual(headers, EN_HEADERS)
        self.assertEqual([row["Serial Number"] for row in rows], ["SN-10001", "SN-10002", "SN-10003"])
        self.assertEqual({row["Current Contract"] for row in rows}, {"CONT-2026-001"})
        response = self.client.get(
            url("supportcontract_coverage", self.warranty.pk), {"export": "provider_csv", "q": "SN-10002"}
        )
        self.assertEqual([row["Serial Number"] for row in parse_csv(response)[1]], ["SN-10002"])

    def test_export_buttons_keep_filters(self):
        response = self.client.get(url("device_coverage"), {"status": "active", "expiring_within_days": 90})
        self.assertContains(response, "Export for Support Provider (CSV)")
        self.assertContains(response, "expiring_within_days=90")
        self.assertContains(response, "export=provider_csv")
        response = self.client.get(url("supportcontract_coverage", self.warranty.pk))
        self.assertContains(response, "Export for Support Provider (CSV)")
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "tr"
        response = self.client.get(url("device_coverage"))
        self.assertContains(response, "Yüklenici Teklif Listesi İndir (CSV)")

    def test_report_table_columns_and_table_export(self):
        self.user.config.set(
            "tables.DeviceCoverageTable.columns",
            ["name", "serial", "status", "manufacturer", "part_number", "asset_tag", "end_of_support", "coverage"],
            commit=True,
        )
        response = self.client.get(url("device_coverage"), {"status": "active"})
        self.assertContains(response, "PN-STOR-A")
        self.assertContains(response, days(180).isoformat())
        response = self.client.get(url("device_coverage"), {"export": "table"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PN-STOR-A", response.content)


class CoreColumnRenderingTests(ExportFixtures, TestCase):
    """Columns registered on VirtualMachineTable, ModuleTable and ModuleTypeTable render and export."""

    COLUMNS = (
        "support_coverage_status", "support_end_date", "support_days_remaining", "support_integrator",
        "support_maintenance_company",
    )

    def render(self, table_class, queryset, columns):
        request = RequestFactory().get("/")
        request.user = User.objects.create_user(username="admin", is_superuser=True)
        with event_tracking(request):
            table = table_class(queryset)
            table._apply_prefetching(columns=columns)
            row = table.rows[0]
            return {name: (str(row.get_cell(name)), row.get_cell_value(name)) for name in columns}

    def test_virtual_machine_table(self):
        vm = make_vm("VM-01")
        cover(self.tpm, vm)
        cells = self.render(VirtualMachineTable, VirtualMachine.objects.filter(pk=vm.pk), self.COLUMNS)
        self.assertIn("Covered", cells["support_coverage_status"][0])
        self.assertEqual(cells["support_end_date"], (days(400).isoformat(), days(400).isoformat()))
        self.assertEqual(cells["support_days_remaining"][1], 400)
        self.assertEqual(cells["support_maintenance_company"], ("Example Maintenance A", "Example Maintenance A"))

    def test_module_table(self):
        module = make_module(self.server, serial="MOD-10001")
        cover(self.warranty, module)
        from dcim.models import Module

        cells = self.render(ModuleTable, Module.objects.filter(pk=module.pk), self.COLUMNS)
        self.assertEqual(cells["support_coverage_status"][1], "Expiring")
        self.assertEqual(cells["support_days_remaining"][1], 60)

    def test_module_type_end_of_support(self):
        module_type = ModuleType.objects.create(manufacturer=make_manufacturer(), model="Linecard-EoL")
        HardwareLifecycleEoL.objects.create(module_type=module_type, end_of_support=days(-5))
        cells = self.render(ModuleTypeTable, ModuleType.objects.filter(pk=module_type.pk), ("end_of_support",))
        self.assertIn("Expired", cells["end_of_support"][0])
        self.assertEqual(cells["end_of_support"][1], days(-5).isoformat())
