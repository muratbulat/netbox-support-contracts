import json

from dcim.models import Device
from dcim.tables import DeviceTable
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import translation
from netbox.context_managers import event_tracking
from rest_framework import status
from users.models import User
from utilities.testing import APITestCase, TestCase

from netbox_support_contracts import navigation
from netbox_support_contracts.choices import ContractStateChoices, ContractTypeChoices, CoverageStatusChoices
from netbox_support_contracts.filtersets import SupportCoverageFilterSet
from netbox_support_contracts.models import Integrator, MaintenanceCompany, SupportContract, SupportCoverage
from netbox_support_contracts.services import annotate_companies, bulk_add_coverage, object_coverage

from . import (
    cover,
    days,
    make_contract,
    make_device,
    make_integrator,
    make_maintenance_company,
    make_manufacturer,
)

PLUGIN = "netbox_support_contracts"


def url(name, *args):
    return reverse(f"plugins:{PLUGIN}:{name}", args=args)


class LifecycleFixtures:
    """A server bought through an integrator, now past OEM warranty and maintained by a TPM company."""

    @classmethod
    def setUpTestData(cls):
        cls.integrator = make_integrator("Example Integrator")
        cls.other_integrator = make_integrator("Example Second Integrator")
        cls.tpm_company = make_maintenance_company("Example Maintenance")
        cls.server = make_device("Server-01", serial="SN-10001")
        cls.server_2 = make_device("Server-02", serial="SN-10002")
        cls.oem = make_contract(
            "CONT-2023-001", start_date=days(-1100), end_date=days(-5), contract_type=ContractTypeChoices.WARRANTY,
            integrator=cls.integrator,
        )
        cls.tpm = make_contract(
            "TPM-2026-001", start_date=days(-4), end_date=days(360), contract_type=ContractTypeChoices.TPM,
            manufacturer=None, maintenance_company=cls.tpm_company,
        )
        cls.oem_row = cover(cls.oem, cls.server)
        cls.oem_row_2 = cover(cls.oem, cls.server_2, integrator=cls.other_integrator, start_date=days(-1000))
        cls.tpm_row = cover(cls.tpm, cls.server)


class ModelTests(LifecycleFixtures, TestCase):
    def test_contract_requires_manufacturer_or_maintenance_company(self):
        with self.assertRaises(ValidationError) as cm:
            SupportContract(contract_number="CONT-2026-900").full_clean()
        self.assertEqual(set(cm.exception.message_dict), {"manufacturer", "maintenance_company"})
        SupportContract(contract_number="TPM-2026-900", maintenance_company=self.tpm_company).full_clean()
        SupportContract(contract_number="CONT-2026-900", manufacturer=make_manufacturer()).full_clean()

    def test_contract_number_unique_per_maintenance_company_without_manufacturer(self):
        with self.assertRaises(ValidationError):
            SupportContract(contract_number="tpm-2026-001", maintenance_company=self.tpm_company).full_clean()
        other = make_maintenance_company("Example Other Maintenance")
        SupportContract(contract_number="TPM-2026-001", maintenance_company=other).full_clean()
        # With a manufacturer set, uniqueness is per manufacturer instead.
        SupportContract(
            contract_number="TPM-2026-001", maintenance_company=self.tpm_company, manufacturer=make_manufacturer()
        ).full_clean()

    def test_effective_properties(self):
        self.assertEqual(self.oem_row.effective_integrator, self.integrator)
        self.assertEqual(self.oem_row.effective_start_date, days(-1100))
        self.assertEqual(self.oem_row_2.effective_integrator, self.other_integrator)
        self.assertEqual(self.oem_row_2.effective_start_date, days(-1000))
        self.assertEqual(self.tpm_row.effective_maintenance_company, self.tpm_company)
        self.assertIsNone(self.oem_row.effective_maintenance_company)
        self.assertEqual(self.tpm.provider, self.tpm_company)
        self.assertEqual(self.oem.provider, self.oem.manufacturer)

    def test_coverage_start_after_end_is_rejected(self):
        with self.assertRaises(ValidationError) as cm:
            SupportCoverage(contract=self.tpm, device=self.server_2, start_date=days(400)).full_clean()
        self.assertIn("end_date", cm.exception.message_dict)

    def test_protected_companies(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.integrator.delete()
        with self.assertRaises(ProtectedError):
            self.tpm_company.delete()

    def test_lifecycle_scenario(self):
        coverage = object_coverage(self.server)
        self.assertEqual(coverage["status"], CoverageStatusChoices.COVERED)
        self.assertEqual(coverage["end_date"], days(360))
        states = {row.contract.contract_number: row.state for row in coverage["rows"]}
        self.assertEqual(
            states, {"CONT-2023-001": ContractStateChoices.EXPIRED, "TPM-2026-001": ContractStateChoices.ACTIVE}
        )

    def test_company_counts(self):
        integrator = annotate_companies(Integrator.objects.filter(pk=self.integrator.pk)).get()
        self.assertEqual((integrator.contract_count, integrator.device_count), (1, 1))  # Server-02 overrides it
        other = annotate_companies(Integrator.objects.filter(pk=self.other_integrator.pk)).get()
        self.assertEqual((other.contract_count, other.device_count), (0, 1))
        company = annotate_companies(MaintenanceCompany.objects.filter(pk=self.tpm_company.pk)).get()
        self.assertEqual((company.contract_count, company.device_count), (1, 1))

    def test_coverage_filter_uses_effective_integrator(self):
        qs = SupportCoverage.objects.all()
        rows = SupportCoverageFilterSet({"integrator_id": [self.integrator.pk]}, qs).qs
        self.assertEqual(list(rows), [self.oem_row])
        rows = SupportCoverageFilterSet({"integrator": [self.other_integrator.slug]}, qs).qs
        self.assertEqual(list(rows), [self.oem_row_2])
        rows = SupportCoverageFilterSet({"maintenance_company_id": [self.tpm_company.pk]}, qs).qs
        self.assertEqual(list(rows), [self.tpm_row])

    def test_bulk_add_with_integrator_and_start_date(self):
        admin = User.objects.create_user(username="admin", is_superuser=True)
        device = make_device("Server-03", serial="SN-10003")
        result = bulk_add_coverage(
            self.oem, user=admin, devices=[device], integrator=self.other_integrator, start_date=days(-900)
        )
        row = result.created[0]
        self.assertEqual((row.integrator, row.start_date), (self.other_integrator, days(-900)))

    def test_core_columns_show_history_and_current_provider(self):
        request = RequestFactory().get("/")
        request.user = User.objects.create_user(username="admin", is_superuser=True)
        columns = ("support_coverage_status", "support_integrator", "support_maintenance_company")
        with event_tracking(request):
            table = DeviceTable(Device.objects.filter(pk=self.server.pk))
            table._apply_prefetching(columns=columns)
            row = table.rows[0]
            self.assertEqual(row.get_cell_value("support_integrator"), "Example Integrator")
            self.assertEqual(row.get_cell_value("support_maintenance_company"), "Example Maintenance")
            self.assertEqual(row.get_cell_value("support_coverage_status"), "Covered")


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class ViewTests(LifecycleFixtures, TestCase):
    def test_company_views_require_permission(self):
        for name in ("integrator_list", "maintenancecompany_list"):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(url(name)).status_code, 403)

    def test_company_list_and_detail(self):
        self.add_permissions(
            f"{PLUGIN}.view_integrator", f"{PLUGIN}.view_maintenancecompany", f"{PLUGIN}.view_supportcontract"
        )
        response = self.client.get(url("integrator_list"))
        self.assertContains(response, "Example Integrator")
        response = self.client.get(self.integrator.get_absolute_url())
        self.assertContains(response, "Example Integrator")
        response = self.client.get(url("maintenancecompany_list"))
        self.assertContains(response, "Example Maintenance")
        response = self.client.get(self.tpm_company.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Support Phone")
        # The contracts table panel loads the filtered contract list.
        response = self.client.get(url("supportcontract_list"), {"maintenance_company_id": self.tpm_company.pk})
        self.assertContains(response, "TPM-2026-001")
        self.assertNotContains(response, "CONT-2023-001")

    def test_company_add_edit_delete_import(self):
        self.add_permissions(
            f"{PLUGIN}.add_integrator", f"{PLUGIN}.change_integrator", f"{PLUGIN}.delete_integrator",
            f"{PLUGIN}.view_integrator", f"{PLUGIN}.add_maintenancecompany", f"{PLUGIN}.view_maintenancecompany",
        )
        self.assertEqual(self.client.get(url("integrator_add")).status_code, 200)
        response = self.client.post(url("integrator_add"), {
            "name": "Example Reseller", "slug": "example-reseller", "contact_email": "sales@example.com",
        })
        self.assertEqual(response.status_code, 302)
        company = Integrator.objects.get(slug="example-reseller")
        response = self.client.post(company.get_absolute_url() + "edit/", {
            "name": "Example Reseller", "slug": "example-reseller", "contact_phone": "+90 000 000 00 00",
        })
        self.assertEqual(response.status_code, 302)
        company.refresh_from_db()
        self.assertEqual(company.contact_phone, "+90 000 000 00 00")
        response = self.client.post(company.get_absolute_url() + "delete/", {"confirm": True})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Integrator.objects.filter(pk=company.pk).exists())
        csv = "name,slug,contact_email\nExample Spares,example-spares,support@example.com\n"
        self.client.post(url("maintenancecompany_bulk_import"), {"data": csv, "format": "csv", "csv_delimiter": ","})
        self.assertTrue(MaintenanceCompany.objects.filter(slug="example-spares").exists())

    def test_contract_form_with_maintenance_company_only(self):
        self.add_permissions(f"{PLUGIN}.add_supportcontract", f"{PLUGIN}.view_maintenancecompany")
        response = self.client.post(url("supportcontract_add"), {
            "contract_number": "TPM-2026-002", "contract_type": "tpm", "maintenance_company": self.tpm_company.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(SupportContract.objects.get(contract_number="TPM-2026-002").manufacturer)

    def test_contract_import_with_companies(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcontract", f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_integrator",
            f"{PLUGIN}.view_maintenancecompany",
        )
        csv = (
            "contract_number,contract_type,integrator,maintenance_company,end_date\n"
            "TPM-2026-010,tpm,Example Integrator,Example Maintenance,2027-06-30\n"
        )
        self.client.post(url("supportcontract_bulk_import"), {"data": csv, "format": "csv", "csv_delimiter": ","})
        contract = SupportContract.objects.get(contract_number="TPM-2026-010")
        self.assertEqual((contract.integrator, contract.maintenance_company), (self.integrator, self.tpm_company))

    def test_device_panel_shows_full_history(self):
        self.add_permissions("dcim.view_device", f"{PLUGIN}.view_supportcoverage")
        response = self.client.get(self.server.get_absolute_url())
        for text in ("CONT-2023-001", "Example Integrator", "TPM-2026-001", "Example Maintenance", "Expired",
                     "Integrator Company", "Maintenance Company"):
            self.assertContains(response, text)

    def test_contract_and_coverage_detail(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage")
        response = self.client.get(self.tpm.get_absolute_url())
        self.assertContains(response, "Example Maintenance")
        self.assertContains(response, f"maintenance_company_id={self.tpm_company.pk}")
        response = self.client.get(self.oem_row.get_absolute_url())
        self.assertContains(response, "Example Integrator")
        self.assertContains(response, "inherited")

    def test_provider_export_columns(self):
        self.add_permissions("dcim.view_device", f"{PLUGIN}.view_supportcoverage")
        response = self.client.get(url("device_coverage"), {"export": "provider_csv", "q": "Server-01"})
        lines = response.content.decode("utf-8-sig").splitlines()
        headers = lines[0].split(",")
        row = dict(zip(headers, lines[1].split(","), strict=True))
        self.assertEqual(row["Integrator Company"], "Example Integrator")
        self.assertEqual(row["Maintenance Company"], "Example Maintenance")
        self.assertEqual(row["Current Contract"], "TPM-2026-001")
        self.assertEqual(row["Current Contract Type"], "Maintenance Company")

    def test_device_report_company_filters(self):
        self.add_permissions("dcim.view_device", f"{PLUGIN}.view_supportcoverage")
        response = self.client.get(url("device_coverage"), {"integrator_id": self.other_integrator.pk})
        self.assertContains(response, "Server-02")
        self.assertNotContains(response, "SN-10001")


class APITests(LifecycleFixtures, APITestCase):
    model = Integrator

    def test_company_endpoints(self):
        self.add_permissions(f"{PLUGIN}.view_integrator", f"{PLUGIN}.view_maintenancecompany")
        response = self.client.get(reverse(f"plugins-api:{PLUGIN}-api:integrator-list"), **self.header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        integrator = {row["name"]: row for row in response.data["results"]}["Example Integrator"]
        self.assertEqual((integrator["contract_count"], integrator["device_count"]), (1, 1))
        response = self.client.get(
            reverse(f"plugins-api:{PLUGIN}-api:maintenancecompany-detail", args=[self.tpm_company.pk]), **self.header
        )
        self.assertEqual(response.data["slug"], "example-maintenance")

    def test_company_create(self):
        self.add_permissions(f"{PLUGIN}.add_maintenancecompany")
        response = self.client.post(
            reverse(f"plugins-api:{PLUGIN}-api:maintenancecompany-list"),
            {"name": "Example Field Service", "slug": "example-field-service", "contact_phone": "0000"},
            format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_contract_and_coverage_fields(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage")
        response = self.client.get(
            reverse(f"plugins-api:{PLUGIN}-api:supportcontract-detail", args=[self.tpm.pk]), **self.header
        )
        self.assertIsNone(response.data["manufacturer"])
        self.assertEqual(response.data["maintenance_company"]["id"], self.tpm_company.pk)
        response = self.client.get(
            reverse(f"plugins-api:{PLUGIN}-api:supportcoverage-detail", args=[self.oem_row_2.pk]), **self.header
        )
        self.assertEqual(response.data["integrator"]["id"], self.other_integrator.pk)
        self.assertEqual(response.data["effective_integrator"]["id"], self.other_integrator.pk)
        self.assertEqual(response.data["effective_start_date"], days(-1000).isoformat())

    def test_create_tpm_contract_without_manufacturer(self):
        self.add_permissions(f"{PLUGIN}.add_supportcontract", f"{PLUGIN}.view_maintenancecompany")
        response = self.client.post(
            reverse(f"plugins-api:{PLUGIN}-api:supportcontract-list"),
            {"contract_number": "TPM-2026-003", "contract_type": "tpm", "maintenance_company": self.tpm_company.pk},
            format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response = self.client.post(
            reverse(f"plugins-api:{PLUGIN}-api:supportcontract-list"),
            {"contract_number": "TPM-2026-004"}, format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_graphql(self):
        self.add_permissions(
            f"{PLUGIN}.view_integrator", f"{PLUGIN}.view_maintenancecompany", f"{PLUGIN}.view_supportcontract"
        )
        query = (
            "{ integrator_list { name support_contracts { contract_number } } "
            "maintenance_company_list { name support_contracts { contract_number maintenance_company { name } } } }"
        )
        response = self.client.post(
            reverse("graphql"), data=json.dumps({"query": query}), content_type="application/json", **self.header
        )
        payload = json.loads(response.content)
        self.assertNotIn("errors", payload, payload.get("errors"))
        integrators = {row["name"]: row for row in payload["data"]["integrator_list"]}
        self.assertEqual(integrators["Example Integrator"]["support_contracts"], [{"contract_number": "CONT-2023-001"}])
        company = payload["data"]["maintenance_company_list"][0]
        self.assertEqual(company["support_contracts"][0]["maintenance_company"]["name"], "Example Maintenance")


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class TranslationTests(LifecycleFixtures, TestCase):
    STRINGS = (
        (lambda: Integrator._meta.verbose_name, "integrator company", "entegratör firma"),
        (lambda: Integrator._meta.verbose_name_plural, "integrator companies", "entegratör firmalar"),
        (lambda: MaintenanceCompany._meta.verbose_name, "maintenance company", "bakım firması"),
        (lambda: MaintenanceCompany._meta.verbose_name_plural, "maintenance companies", "bakım firmaları"),
        (lambda: MaintenanceCompany._meta.get_field("contact_phone").verbose_name, "support phone", "destek telefonu"),
        (lambda: Integrator._meta.get_field("portal_url").verbose_name, "website / portal URL",
         "web sitesi / portal URL'si"),
        (lambda: navigation.menu.groups[1].label, "Partners & Providers", "Firmalar ve Sağlayıcılar"),
        (lambda: navigation.menu.groups[1].items[0].link_text, "Integrator Companies", "Entegratör Firmalar"),
        (lambda: navigation.menu.groups[1].items[1].link_text, "Maintenance Companies", "Bakım Firmaları"),
        (lambda: navigation.menu.groups[0].items[2].link_text, "Device Coverage", "Cihaz Kapsamı"),
        (lambda: navigation.menu.groups[2].items[0].link_text, "Hardware EoL", "Donanım EoL"),
    )

    def test_strings(self):
        for getter, english, turkish in self.STRINGS:
            for language, expected in (("tr", turkish), ("en", english), ("tr", turkish)):
                with self.subTest(expected=expected), translation.override(language):
                    self.assertEqual(str(getter()), expected)

    def test_pages_in_turkish(self):
        self.add_permissions(
            f"{PLUGIN}.view_maintenancecompany", "dcim.view_device", f"{PLUGIN}.view_supportcoverage",
        )
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "tr"
        response = self.client.get(self.tpm_company.get_absolute_url())
        self.assertContains(response, "Destek Telefonu")
        self.assertContains(response, "Destek Portalı URL&#x27;si")
        response = self.client.get(self.server.get_absolute_url())
        self.assertContains(response, "Entegratör Firma")
        self.assertContains(response, "Bakım Firması")
