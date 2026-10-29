from dcim.models import Device, DeviceType, ModuleType
from dcim.tables import DeviceTable, DeviceTypeTable, ModuleTable, ModuleTypeTable
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import translation
from netbox.context_managers import event_tracking
from netbox.registry import registry
from users.models import User
from utilities.testing import TestCase
from virtualization.tables import VirtualMachineTable

from netbox_support_contracts.choices import ContractTypeChoices
from netbox_support_contracts.models import HardwareLifecycleEoL
from netbox_support_contracts.tables import register_core_columns

from . import cover, days, make_contract, make_device, make_device_type, make_integrator, make_maintenance_company

PLUGIN = "netbox_support_contracts"
COVERAGE_COLUMNS = (
    "support_coverage_status", "support_end_date", "support_days_remaining", "support_integrator",
    "support_maintenance_company",
)


class RegistrationTests(TestCase):
    def test_columns_registered_on_core_tables(self):
        for table in (DeviceTable, VirtualMachineTable, ModuleTable):
            with self.subTest(table=table.__name__):
                self.assertTrue(set(COVERAGE_COLUMNS) <= set(registry["tables"][table]))
        for table in (DeviceTypeTable, ModuleTypeTable):
            with self.subTest(table=table.__name__):
                self.assertIn("end_of_support", registry["tables"][table])

    def test_registration_is_idempotent(self):
        register_core_columns()
        register_core_columns()
        registered = [name for name in registry["tables"][DeviceTable] if name in COVERAGE_COLUMNS]
        self.assertEqual(len(registered), len(COVERAGE_COLUMNS))

    def test_columns_offered_in_table_configuration(self):
        for table in (DeviceTable, VirtualMachineTable, ModuleTable):
            with self.subTest(table=table.__name__):
                names = table(table.Meta.model.objects.none()).columns.names()
                self.assertTrue(set(COVERAGE_COLUMNS) <= set(names))

    def test_headers_translate(self):
        expected = {
            "support_coverage_status": ("Support Coverage", "Destek Kapsamı"),
            "support_end_date": ("Support End Date", "Destek Bitiş Tarihi"),
            "support_days_remaining": ("Support Days Remaining", "Destek Kalan Gün"),
            "support_integrator": ("Integrator Company", "Entegratör Firma"),
            "support_maintenance_company": ("Maintenance Company", "Bakım Firması"),
        }
        for name, (english, turkish) in expected.items():
            column = registry["tables"][DeviceTable][name]
            for language, text in (("tr", turkish), ("en", english), ("tr", turkish)):
                with self.subTest(column=name, language=language), translation.override(language):
                    self.assertEqual(str(column.verbose_name), text)
        eos = registry["tables"][DeviceTypeTable]["end_of_support"]
        with translation.override("tr"):
            self.assertEqual(str(eos.verbose_name), "Destek Sonu")
        with translation.override("en"):
            self.assertEqual(str(eos.verbose_name), "End of Support")


class RenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.device_type = make_device_type("Storage-Array")
        cls.covered = make_device("Storage-01", device_type=cls.device_type)
        cls.open_ended = make_device("Storage-02")
        cls.lapsed = make_device("Storage-03")
        cls.uncovered = make_device("Storage-04")
        tpm_a = make_contract(
            "CONT-2026-001", end_date=days(45), contract_type=ContractTypeChoices.TPM,
            maintenance_company=make_maintenance_company("Example Maintenance A"),
        )
        tpm_b = make_contract(
            "CONT-2026-002", end_date=days(20), maintenance_company=make_maintenance_company("Example Maintenance B")
        )
        # Expired OEM warranty bought through an integrator: purchase history outlives the warranty.
        old = make_contract(
            "CONT-2025-001", end_date=days(-10), contract_type=ContractTypeChoices.WARRANTY,
            integrator=make_integrator("Example Distributor"),
        )
        cover(tpm_a, cls.covered)
        cover(tpm_b, cls.covered)
        cover(old, cls.covered)
        cover(
            make_contract("CONT-2026-003", end_date=None, maintenance_company=make_maintenance_company("Example TPM")),
            cls.open_ended,
        )
        cover(old, cls.lapsed)
        HardwareLifecycleEoL.objects.create(device_type=cls.device_type, end_of_support=days(-1))

    def cells(self, user, device):
        request = RequestFactory().get("/")
        request.user = user
        with event_tracking(request):
            table = DeviceTable(Device.objects.filter(pk=device.pk))
            table._apply_prefetching(columns=COVERAGE_COLUMNS)
            row = table.rows[0]
            return {name: str(row.get_cell(name)) for name in COVERAGE_COLUMNS}, {
                name: row.get_cell_value(name) for name in COVERAGE_COLUMNS
            }

    def admin(self):
        return User.objects.create_user(username="admin", is_superuser=True)

    def test_covered_device(self):
        cells, values = self.cells(self.admin(), self.covered)
        self.assertIn("text-bg-yellow", cells["support_coverage_status"])  # 45 days left, within 90-day window
        self.assertEqual(cells["support_end_date"], days(45).isoformat())
        self.assertIn("45 days left", cells["support_days_remaining"])
        self.assertEqual(cells["support_maintenance_company"], "Example Maintenance A, Example Maintenance B")
        self.assertEqual(cells["support_integrator"], "Example Distributor")
        self.assertEqual(values["support_coverage_status"], "Expiring")
        self.assertEqual(values["support_days_remaining"], 45)

    def test_open_ended_lapsed_and_uncovered(self):
        admin = self.admin()
        cells, values = self.cells(admin, self.open_ended)
        self.assertEqual(cells["support_end_date"], "Open-ended")
        self.assertIn("Covered", cells["support_coverage_status"])
        self.assertEqual(values["support_days_remaining"], "")
        cells, values = self.cells(admin, self.lapsed)
        self.assertIn("Expired", cells["support_coverage_status"])
        self.assertEqual(cells["support_maintenance_company"], "—")  # expired contracts no longer provide support
        self.assertEqual(cells["support_integrator"], "Example Distributor")
        cells, values = self.cells(admin, self.uncovered)
        self.assertIn("Not covered", cells["support_coverage_status"])
        self.assertEqual(cells["support_end_date"], "—")

    def test_hidden_without_coverage_permission(self):
        cells, values = self.cells(User.objects.create_user(username="viewer"), self.covered)
        self.assertEqual(set(cells.values()), {"—"})
        self.assertEqual(set(values.values()), {""})

    def test_turkish_rendering(self):
        admin = self.admin()
        with translation.override("tr"):
            cells, values = self.cells(admin, self.covered)
            self.assertIn("Süresi Yakında Dolacak", cells["support_coverage_status"])
            self.assertIn("45 gün kaldı", cells["support_days_remaining"])
            cells, _values = self.cells(admin, self.uncovered)
            self.assertIn("Kapsam Dışı", cells["support_coverage_status"])

    def test_prefetching_avoids_per_row_queries(self):
        request = RequestFactory().get("/")
        request.user = self.admin()
        with event_tracking(request):
            table = DeviceTable(Device.objects.all())
            table._apply_prefetching(columns=COVERAGE_COLUMNS)
            rows = list(table.rows)
            with self.assertNumQueries(0):
                for row in rows:
                    for name in COVERAGE_COLUMNS:
                        str(row.get_cell(name))

    def test_device_type_end_of_support(self):
        request = RequestFactory().get("/")
        request.user = self.admin()
        with event_tracking(request):
            table = DeviceTypeTable(DeviceType.objects.filter(pk=self.device_type.pk))
            cell = str(table.rows[0].get_cell("end_of_support"))
            self.assertIn(days(-1).isoformat(), cell)
            self.assertIn("Expired", cell)
            table = ModuleTypeTable(ModuleType.objects.none())
            self.assertIn("end_of_support", table.columns.names())


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class DeviceListPageTests(TestCase):
    def test_columns_on_device_list(self):
        device = make_device("Storage-01")
        tpm = make_maintenance_company("Example Maintenance A")
        cover(make_contract("CONT-2026-001", end_date=days(45), maintenance_company=tpm), device)
        self.add_permissions("dcim.view_device", f"{PLUGIN}.view_supportcoverage")
        self.user.config.set("tables.DeviceTable.columns", ["name", *COVERAGE_COLUMNS], commit=True)
        response = self.client.get(reverse("dcim:device_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Maintenance Company")
        self.assertContains(response, "Example Maintenance A")
        self.assertContains(response, "45 days left")
