from dcim.models import Device
from django.test import RequestFactory, override_settings
from django.urls import NoReverseMatch, reverse
from extras.models import Dashboard
from netbox.context_managers import event_tracking
from utilities.testing import TestCase

from netbox_support_contracts.filtersets import SupportContractFilterSet, SupportCoverageFilterSet
from netbox_support_contracts.forms import SupportContractForm
from netbox_support_contracts.models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from netbox_support_contracts.services import VIEW_COST_PERMISSION
from netbox_support_contracts.tables import SupportContractTable
from netbox_support_contracts.widgets import SupportCoverageSummaryWidget

from . import cover, days, make_contract, make_device, make_device_type, make_integrator, make_manufacturer, make_vm

PLUGIN = "netbox_support_contracts"
COST = "1,200.00 USD"


def url(name, *args):
    return reverse(f"plugins:{PLUGIN}:{name}", args=args)


def csv_import(client, view, csv):
    return client.post(url(view), {"data": csv, "format": "csv", "csv_delimiter": ","})


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class ViewTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.contract = make_contract(
            "CONT-2026-001",
            end_date=days(45),
            service_level="24x7x4",
            integrator=make_integrator("Example Partner"),
            cost="1200.00",
            currency="USD",
            portal_url="https://support.example.com/contracts/CONT-2026-001",
        )
        cls.device_type = make_device_type("Switch-48")
        cls.device = make_device("Device-01", serial="SN-10001", device_type=cls.device_type)
        cls.device_2 = make_device("Device-02", serial="SN-10002", device_type=cls.device_type)
        cls.coverage = cover(cls.contract, cls.device)
        cls.eol = HardwareLifecycleEoL.objects.create(device_type=cls.device_type, end_of_support=days(-1))


class ModelViewTests(ViewTestBase):
    def test_views_are_default_closed(self):
        for name in ("supportcontract_list", "supportcoverage_list", "hardwarelifecycleeol_list"):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(url(name)).status_code, 403)
        self.assertEqual(self.client.get(self.contract.get_absolute_url()).status_code, 403)

    def test_list_and_detail_views(self):
        self.add_permissions(
            f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage", f"{PLUGIN}.view_hardwarelifecycleeol"
        )
        for name in ("supportcontract_list", "supportcoverage_list", "hardwarelifecycleeol_list"):
            with self.subTest(view=name):
                response = self.client.get(url(name))
                self.assertEqual(response.status_code, 200)
        response = self.client.get(url("supportcontract_list"))
        self.assertContains(response, "CONT-2026-001")
        self.assertContains(response, "45 days left")
        self.assertContains(response, "text-bg-yellow")

        for obj in (self.contract, self.coverage, self.eol):
            with self.subTest(obj=obj):
                self.assertEqual(self.client.get(obj.get_absolute_url()).status_code, 200)
        response = self.client.get(self.contract.get_absolute_url())
        self.assertContains(response, "Contract Overview")
        self.assertContains(response, "Covered Devices &amp; Objects")

    def test_list_sorting_by_computed_columns(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage")
        for sort in ("state", "-days_remaining", "end_date", "device_count"):
            with self.subTest(sort=sort):
                self.assertEqual(self.client.get(url("supportcontract_list"), {"sort": sort}).status_code, 200)
        for sort in ("state", "effective_end_date", "-days_remaining", "device_serial", "site"):
            with self.subTest(sort=sort):
                self.assertEqual(self.client.get(url("supportcoverage_list"), {"sort": sort}).status_code, 200)

    def test_csv_export(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract")
        response = self.client.get(url("supportcontract_list"), {"export": "table"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CONT-2026-001", response.content)
        self.assertNotIn(b"1,200.00", response.content)

    def test_add_and_edit_contract(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcontract", f"{PLUGIN}.change_supportcontract", "dcim.view_manufacturer"
        )
        self.assertEqual(self.client.get(url("supportcontract_add")).status_code, 200)
        response = self.client.post(url("supportcontract_add"), {
            "manufacturer": make_manufacturer().pk,
            "contract_number": "CONT-2026-002",
            "contract_type": "vendor_support",
            "end_date": days(10).isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(SupportContract.objects.filter(contract_number="CONT-2026-002").exists())

    def test_bulk_import_contracts(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcontract", f"{PLUGIN}.view_supportcontract", "dcim.view_manufacturer"
        )
        csv = "manufacturer,contract_number,service_level,end_date\nAcme Corp,CONT-2026-010,8x5xNBD,2027-01-31\n"
        response = csv_import(self.client, "supportcontract_bulk_import", csv)
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(SupportContract.objects.filter(contract_number="CONT-2026-010").exists())

    def test_bulk_import_coverage(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcoverage", f"{PLUGIN}.view_supportcontract",
            "dcim.view_device",
        )
        csv = "contract,manufacturer,device,sku\nCONT-2026-001,Acme Corp,Device-02,SUP-24X7\n"
        response = csv_import(self.client, "supportcoverage_bulk_import", csv)
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(SupportCoverage.objects.filter(device=self.device_2, sku="SUP-24X7").exists())

    def test_bulk_edit_contracts(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.change_supportcontract")
        response = self.client.post(url("supportcontract_bulk_edit"), {
            "pk": [self.contract.pk], "_apply": True, "service_level": "8x5xNBD",
        })
        self.assertEqual(response.status_code, 302)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.service_level, "8x5xNBD")

    def test_state_filter_on_list(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract")
        make_contract("CONT-2026-003", end_date=days(-3))
        response = self.client.get(url("supportcontract_list"), {"state": "expired"})
        self.assertContains(response, "CONT-2026-003")
        self.assertNotContains(response, "CONT-2026-001")


class CostVisibilityTests(ViewTestBase):
    def setUp(self):
        super().setUp()
        self.add_permissions(f"{PLUGIN}.view_supportcontract")

    def test_cost_hidden_without_permission(self):
        self.assertNotContains(self.client.get(url("supportcontract_list")), COST)
        self.assertNotContains(self.client.get(self.contract.get_absolute_url()), COST)

    def test_cost_visible_with_permission(self):
        self.add_permissions(VIEW_COST_PERMISSION)
        response = self.client.get(url("supportcontract_list"), {"sort": "cost_display"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(self.contract.get_absolute_url()), COST)

    def test_table_excludes_cost_column(self):
        request = RequestFactory().get("/")
        request.user = self.user
        with event_tracking(request):
            self.assertNotIn("cost_display", SupportContractTable(SupportContract.objects.all()).columns.names())
            self.add_permissions(VIEW_COST_PERMISSION)
            request.user = type(self.user).objects.get(pk=self.user.pk)
            self.assertIn("cost_display", SupportContractTable(SupportContract.objects.all()).columns.names())

    def test_form_drops_cost_fields(self):
        request = RequestFactory().get("/")
        request.user = self.user
        with event_tracking(request):
            self.assertNotIn("cost", SupportContractForm(instance=self.contract).fields)
            self.assertNotIn("currency", SupportContractForm(instance=self.contract).fields)

    def test_edit_without_permission_preserves_cost(self):
        self.add_permissions(f"{PLUGIN}.change_supportcontract", "dcim.view_manufacturer")
        response = self.client.post(self.contract.get_absolute_url() + "edit/", {
            "manufacturer": self.contract.manufacturer.pk,
            "contract_number": self.contract.contract_number,
            "contract_type": self.contract.contract_type,
            "end_date": days(60).isoformat(),
            "cost": "1.00",
        })
        self.assertEqual(response.status_code, 302)
        self.contract.refresh_from_db()
        self.assertEqual(str(self.contract.cost), "1200.00")
        self.assertEqual(self.contract.end_date, days(60))


class CoverageWorkflowTests(ViewTestBase):
    def setUp(self):
        super().setUp()
        self.add_permissions(
            f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage", "dcim.view_device",
            "virtualization.view_virtualmachine",
        )

    def test_covered_objects_tab(self):
        response = self.client.get(url("supportcontract_coverage", self.contract.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SN-10001")
        self.add_permissions(
            f"{PLUGIN}.delete_supportcoverage", f"{PLUGIN}.change_supportcoverage", f"{PLUGIN}.add_supportcoverage"
        )
        response = self.client.get(url("supportcontract_coverage", self.contract.pk))
        # Only NetBox's native bulk controls remain at the bottom; the top "Add Devices / Serials" button stays.
        self.assertNotContains(response, "Remove Selected")
        self.assertContains(response, url("supportcoverage_bulk_edit"))
        self.assertContains(response, url("supportcoverage_bulk_delete"))
        self.assertContains(response, url("supportcontract_add_devices", self.contract.pk))
        response = self.client.get(url("supportcontract_coverage", self.contract.pk), {"object_type": "device"})
        self.assertEqual(response.status_code, 200)

    def test_add_devices_requires_add_permission(self):
        self.assertEqual(self.client.get(url("supportcontract_add_devices", self.contract.pk)).status_code, 403)

    def test_add_devices_by_serial_with_review(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage")
        page = url("supportcontract_add_devices", self.contract.pk)
        get_response = self.client.get(page)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'id="htmx-modal"')
        response = self.client.post(page, {"serials": "sn-10001\nSN-10002\nSN-99999"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 added")
        self.assertContains(response, "1 already covered")
        self.assertContains(response, "1 not found")
        self.assertContains(response, "SN-99999</textarea>")
        self.assertTrue(SupportCoverage.objects.filter(contract=self.contract, device=self.device_2).exists())

    def test_add_devices_all_matched_redirects(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage")
        vm = make_vm()
        response = self.client.post(url("supportcontract_add_devices", self.contract.pk), {
            "devices": [self.device_2.pk], "virtual_machines": [vm.pk],
        })
        self.assertRedirects(response, url("supportcontract_coverage", self.contract.pk), fetch_redirect_response=False)
        self.assertEqual(SupportCoverage.objects.filter(contract=self.contract).count(), 3)

    def test_add_devices_by_filter(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage", "dcim.view_devicetype")
        response = self.client.post(url("supportcontract_add_devices", self.contract.pk), {
            "device_type": [self.device_type.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SupportCoverage.objects.filter(contract=self.contract).count(), 2)

    def test_add_devices_empty_submission(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage")
        response = self.client.post(url("supportcontract_add_devices", self.contract.pk), {})
        self.assertContains(response, "Pick at least one object")

    def test_native_bulk_delete_unlinks_coverage(self):
        with self.assertRaises(NoReverseMatch):
            url("supportcontract_remove_coverage", self.contract.pk)
        coverage_tab = url("supportcontract_coverage", self.contract.pk)
        data = {"pk": [self.coverage.pk], "_confirm": True, "confirm": True, "return_url": coverage_tab}
        self.assertEqual(self.client.post(url("supportcoverage_bulk_delete"), data).status_code, 403)
        self.add_permissions(f"{PLUGIN}.delete_supportcoverage")
        response = self.client.post(url("supportcoverage_bulk_delete"), data)
        self.assertRedirects(response, coverage_tab, fetch_redirect_response=False)
        self.assertFalse(SupportCoverage.objects.filter(pk=self.coverage.pk).exists())
        self.assertTrue(Device.objects.filter(pk=self.device.pk).exists())

    def test_device_panel(self):
        response = self.client.get(self.device.get_absolute_url())
        self.assertContains(response, "Support &amp; Warranty Coverage")
        self.assertContains(response, "CONT-2026-001")
        self.assertContains(response, 'data-flag="eol_under_contract"')
        response = self.client.get(self.device_2.get_absolute_url())
        self.assertContains(response, "Not covered")

    def test_virtual_machine_panel(self):
        vm = make_vm()
        cover(self.contract, vm)
        response = self.client.get(vm.get_absolute_url())
        self.assertContains(response, "Support &amp; Warranty Coverage")
        self.assertContains(response, "CONT-2026-001")

    def test_device_type_lifecycle_panel(self):
        self.add_permissions("dcim.view_devicetype", f"{PLUGIN}.view_hardwarelifecycleeol")
        response = self.client.get(self.device_type.get_absolute_url())
        self.assertContains(response, "Vendor Lifecycle (EoL)")

    def test_device_coverage_report(self):
        response = self.client.get(url("device_coverage"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Device-01")
        response = self.client.get(url("device_coverage"), {"coverage": "none"})
        self.assertContains(response, "Device-02")
        self.assertNotContains(response, "SN-10001")
        self.assertEqual(self.client.get(url("device_coverage"), {"sort": "coverage"}).status_code, 200)

    def test_dashboard_widget(self):
        request = RequestFactory().get("/")
        request.user = self.user
        html = SupportCoverageSummaryWidget().render(request)
        self.assertIn("Not Covered", html)
        self.assertIn("CONT-2026-001", html)
        self.assertIn("?coverage=none", html)
        add_url = reverse("extras:dashboardwidget_add")
        response = self.client.get(add_url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support Coverage Summary")
        Dashboard.objects.create(user=self.user)  # NetBox creates it on the user's first home page visit
        post_response = self.client.post(add_url, {
            "widget_class": f"{PLUGIN}.SupportCoverageSummaryWidget",
            "config-limit": 8,
        })
        self.assertEqual(post_response.status_code, 200)
        self.assertEqual(post_response.headers["HX-Redirect"], reverse("home"))
        self.user.dashboard.refresh_from_db()
        widgets = list(self.user.dashboard.config.values())
        self.assertEqual(widgets[-1]["class"], f"{PLUGIN}.SupportCoverageSummaryWidget")
        self.assertEqual(widgets[-1]["config"]["limit"], 8)
        self.assertEqual(widgets[-1]["title"], "Support Coverage Summary")


class GlobalSearchTests(ViewTestBase):
    def test_contract_number_is_searchable(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract")
        with self.captureOnCommitCallbacks(execute=True):  # the search cache is written on commit
            contract = make_contract("CONT-2026-077", integrator=make_integrator("Example Partner"))
        response = self.client.get(reverse("search"), {"q": "CONT-2026-077"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, contract.get_absolute_url())


class FilterSetTests(ViewTestBase):
    def test_contract_filters(self):
        expired = make_contract("CONT-2026-003", end_date=days(-3))
        qs = SupportContract.objects.all()
        self.assertEqual(list(SupportContractFilterSet({"state": ["expired"]}, qs).qs), [expired])
        self.assertEqual(list(SupportContractFilterSet({"expiring_within_days": 60}, qs).qs), [self.contract])
        self.assertEqual(list(SupportContractFilterSet({"expiring_within_days": 10}, qs).qs), [])
        self.assertEqual(list(SupportContractFilterSet({"expiring_within_days": 10**12}, qs).qs), [self.contract])
        self.assertEqual(list(SupportContractFilterSet({"device_id": [self.device.pk]}, qs).qs), [self.contract])
        self.assertEqual(list(SupportContractFilterSet({"q": "example partner"}, qs).qs), [self.contract])
        self.assertEqual(SupportContractFilterSet({"q": "acme"}, qs).qs.count(), 2)

    def test_coverage_filters(self):
        override = cover(make_contract("CONT-2026-004", end_date=days(300)), self.device_2, end_date=days(-1))
        qs = SupportCoverage.objects.all()
        self.assertEqual(list(SupportCoverageFilterSet({"state": ["expired"]}, qs).qs), [override])
        self.assertEqual(list(SupportCoverageFilterSet({"state": ["expiring"]}, qs).qs), [self.coverage])
        self.assertEqual(list(SupportCoverageFilterSet({"q": "SN-10002"}, qs).qs), [override])
        site = Device.objects.get(pk=self.device.pk).site
        self.assertEqual(SupportCoverageFilterSet({"site_id": [site.pk]}, qs).qs.count(), 2)
