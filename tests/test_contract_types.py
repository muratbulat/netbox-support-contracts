import json

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import translation
from rest_framework import status
from utilities.testing import APITestCase, TestCase

from netbox_support_contracts.choices import ContractTypeChoices
from netbox_support_contracts.filtersets import (
    DeviceCoverageFilterSet,
    SupportContractFilterSet,
    SupportCoverageFilterSet,
)
from netbox_support_contracts.forms import SupportCoverageBulkEditForm
from netbox_support_contracts.models import SupportContract, SupportCoverage
from netbox_support_contracts.services import annotate_contracts, bulk_add_coverage
from netbox_support_contracts.tables import SupportContractTable, SupportCoverageTable

from . import cover, days, make_contract, make_device, make_manufacturer, make_vm

PLUGIN = "netbox_support_contracts"


def url(name, *args):
    return reverse(f"plugins:{PLUGIN}:{name}", args=args)


class ContractFixtures:
    @classmethod
    def setUpTestData(cls):
        cls.storage_a = make_device("Storage-01", serial="SN-10001")
        cls.storage_b = make_device("Storage-02", serial="SN-10002")
        cls.server = make_device("Server-01", serial="SN-10003")
        cls.tpm_2026 = make_contract(
            "CONT-2026-001", end_date=days(30), contract_type=ContractTypeChoices.TPM, reseller="Example Maintenance A"
        )
        cls.tpm_2027 = make_contract(
            "CONT-2027-001", end_date=days(395), contract_type=ContractTypeChoices.TPM,
            reseller="Example Maintenance B",
        )
        cls.warranty = make_contract("CONT-2026-002", end_date=days(700), contract_type=ContractTypeChoices.WARRANTY)
        cls.software = make_contract(
            "CONT-2026-003", end_date=days(200), contract_type=ContractTypeChoices.SOFTWARE,
            manufacturer=make_manufacturer("Example Software"),
        )
        cls.row_a = cover(cls.tpm_2026, cls.storage_a)
        cls.row_b = cover(cls.tpm_2026, cls.storage_b)
        cover(cls.warranty, cls.server)
        cls.office = SupportCoverage.objects.create(contract=cls.software, sku="OFFICE-2021-STD", quantity=250)
        cls.veeam = cover(cls.software, make_vm("VM-Backup-01"), sku="VEEAM-VUL-10", quantity=10)


class ContractTypeModelTests(ContractFixtures, TestCase):
    def test_default_and_color(self):
        contract = make_contract("CONT-2026-099")
        self.assertEqual(contract.contract_type, ContractTypeChoices.VENDOR_SUPPORT)
        self.assertEqual(contract.get_contract_type_color(), "cyan")
        self.assertEqual(self.tpm_2026.get_contract_type_color(), "purple")
        self.assertIn("contract_type", SupportContract.clone_fields)

    def test_contract_filters(self):
        qs = SupportContract.objects.all()
        result = SupportContractFilterSet({"contract_type": ["tpm"]}, qs).qs
        self.assertEqual(set(result), {self.tpm_2026, self.tpm_2027})
        result = SupportContractFilterSet({"contract_type": ["software", "warranty"]}, qs).qs
        self.assertEqual(set(result), {self.software, self.warranty})

    def test_quantity_and_sku_count(self):
        software = annotate_contracts(SupportContract.objects.filter(pk=self.software.pk)).get()
        self.assertEqual((software.sku_count, software.vm_count, software.device_count), (1, 1, 0))
        self.assertEqual(self.office.quantity, 250)
        tpm = annotate_contracts(SupportContract.objects.filter(pk=self.tpm_2026.pk)).get()
        self.assertEqual(tpm.sku_count, 0)

    def test_bulk_add_passes_quantity(self):
        from users.models import User

        admin = User.objects.create_user(username="admin", is_superuser=True)
        result = bulk_add_coverage(self.software, user=admin, devices=[self.server], sku="VEEAM-SOCKET", quantity=2)
        self.assertEqual(result.created[0].quantity, 2)

    def test_coverage_filters_by_reseller_and_type(self):
        qs = SupportCoverage.objects.all()
        self.assertEqual(set(SupportCoverageFilterSet({"reseller": "maintenance a"}, qs).qs), {self.row_a, self.row_b})
        software = SupportCoverageFilterSet({"contract_type": ["software"]}, qs).qs
        self.assertEqual(set(software), {self.office, self.veeam})
        self.assertEqual(set(SupportCoverageFilterSet({"q": "Maintenance A"}, qs).qs), {self.row_a, self.row_b})
        self.assertEqual(list(SupportCoverageFilterSet({"quantity": [250]}, qs).qs), [self.office])

    def test_device_report_filters_by_reseller_and_type(self):
        from dcim.models import Device

        qs = Device.objects.all()
        self.assertEqual(
            set(DeviceCoverageFilterSet({"reseller": "Maintenance A"}, qs).qs), {self.storage_a, self.storage_b}
        )
        self.assertEqual(list(DeviceCoverageFilterSet({"contract_type": ["warranty"]}, qs).qs), [self.server])
        cover(self.tpm_2027, self.storage_a)  # a second matching contract must not duplicate the device
        self.assertEqual(DeviceCoverageFilterSet({"contract_type": ["tpm"]}, qs).qs.count(), 2)

    def test_tables(self):
        contracts = SupportContractTable(annotate_contracts(SupportContract.objects.all()))
        self.assertIn("contract_type", contracts.Meta.default_columns)
        self.assertIn("sku_count", contracts.columns.names())
        coverage = SupportCoverageTable(SupportCoverage.objects.all())
        self.assertIn("reseller", coverage.Meta.default_columns)
        self.assertIn("quantity", coverage.Meta.default_columns)
        self.assertIn("contract_type", coverage.columns.names())


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class ContractTypeViewTests(ContractFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.add_permissions(
            f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage", "dcim.view_device",
            "virtualization.view_virtualmachine",
        )

    def test_contract_page_shows_type_and_sku_count(self):
        response = self.client.get(self.software.get_absolute_url())
        self.assertContains(response, "Software License / Subscription")
        self.assertContains(response, "text-bg-indigo")
        self.assertContains(response, "Licenses / SKUs")
        self.assertContains(response, "?object_type=none")

    def test_contract_list_type_column_and_filter(self):
        response = self.client.get(url("supportcontract_list"), {"contract_type": "tpm"})
        self.assertContains(response, "Third-Party Maintenance (TPM)")
        self.assertContains(response, "CONT-2027-001")
        self.assertNotContains(response, "CONT-2026-003")

    def test_coverage_detail_shows_type_reseller_quantity(self):
        response = self.client.get(self.office.get_absolute_url())
        self.assertContains(response, "Software License / Subscription")
        self.assertContains(response, "250")
        response = self.client.get(self.row_a.get_absolute_url())
        self.assertContains(response, "Example Maintenance A")

    def test_device_panel_shows_type_and_reseller(self):
        response = self.client.get(self.storage_a.get_absolute_url())
        self.assertContains(response, "Reseller / Provider")
        self.assertContains(response, "Example Maintenance A")
        self.assertContains(response, "Third-Party Maintenance (TPM)")

    def test_module_panel(self):
        from . import make_module

        self.add_permissions("dcim.view_module")
        module = make_module(self.server)
        cover(self.warranty, module)
        response = self.client.get(module.get_absolute_url())
        self.assertContains(response, "Support &amp; Warranty Coverage")
        self.assertContains(response, "OEM Warranty")

    def test_device_report_filter(self):
        response = self.client.get(url("device_coverage"), {"reseller": "Maintenance A"})
        self.assertContains(response, "Storage-01")
        self.assertNotContains(response, "Server-01")

    def test_bulk_edit_moves_devices_to_new_provider_contract(self):
        self.add_permissions(f"{PLUGIN}.change_supportcoverage")
        response = self.client.post(url("supportcoverage_bulk_edit"), {
            "pk": [self.row_a.pk, self.row_b.pk], "_apply": True, "contract": self.tpm_2027.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.tpm_2027.coverage.values_list("device_id", flat=True)),
                         {self.storage_a.pk, self.storage_b.pk})
        self.assertFalse(self.tpm_2026.coverage.exists())
        response = self.client.get(url("supportcoverage_list"), {"reseller": "Maintenance B"})
        self.assertContains(response, "Storage-01")

    def test_bulk_edit_move_rejects_duplicate_coverage(self):
        self.add_permissions(f"{PLUGIN}.change_supportcoverage")
        cover(self.tpm_2027, self.storage_a)
        self.client.post(url("supportcoverage_bulk_edit"), {
            "pk": [self.row_a.pk, self.row_b.pk], "_apply": True, "contract": self.tpm_2027.pk,
        })
        # The whole batch is rolled back: nothing moved.
        self.assertEqual(self.tpm_2026.coverage.count(), 2)

    def test_bulk_edit_form_contract_field(self):
        form = SupportCoverageBulkEditForm()
        self.assertIn("contract", form.fields)
        self.assertIn("quantity", form.nullable_fields)


class ContractTypeAPITests(ContractFixtures, APITestCase):
    model = SupportContract

    def test_rest_api(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage")
        response = self.client.get(
            reverse(f"plugins-api:{PLUGIN}-api:supportcontract-detail", args=[self.software.pk]), **self.header
        )
        self.assertEqual(response.data["contract_type"]["value"], "software")
        self.assertEqual(response.data["sku_count"], 1)
        response = self.client.get(
            reverse(f"plugins-api:{PLUGIN}-api:supportcoverage-detail", args=[self.office.pk]), **self.header
        )
        self.assertEqual(response.data["quantity"], 250)

    def test_api_create_and_bulk_add_quantity(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcontract", f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcontract",
            "dcim.view_manufacturer", "dcim.view_device",
        )
        response = self.client.post(
            reverse(f"plugins-api:{PLUGIN}-api:supportcontract-list"),
            {"manufacturer": make_manufacturer().pk, "contract_number": "CONT-2026-050", "contract_type": "tpm"},
            format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SupportContract.objects.get(contract_number="CONT-2026-050").contract_type, "tpm")
        response = self.client.post(
            reverse(f"plugins-api:{PLUGIN}-api:supportcoverage-bulk-add"),
            {"contract_id": self.software.pk, "device_ids": [self.server.pk], "sku": "VEEAM-SOCKET", "quantity": 4},
            format="json", **self.header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SupportCoverage.objects.get(sku="VEEAM-SOCKET").quantity, 4)

    def test_graphql(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage")
        query = (
            '{ support_contract_list(filters: {contract_type: {exact: "software"}}) '
            "{ contract_number contract_type coverage { sku quantity } } }"
        )
        response = self.client.post(
            reverse("graphql"), data=json.dumps({"query": query}), content_type="application/json", **self.header
        )
        payload = json.loads(response.content)
        self.assertNotIn("errors", payload, payload.get("errors"))
        rows = payload["data"]["support_contract_list"]
        self.assertEqual([row["contract_number"] for row in rows], ["CONT-2026-003"])
        self.assertEqual(rows[0]["contract_type"].lower(), "software")
        self.assertEqual(sorted(c["quantity"] for c in rows[0]["coverage"]), [10, 250])


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class ContractTypeTranslationTests(ContractFixtures, TestCase):
    STRINGS = (
        (lambda: dict(ContractTypeChoices)["warranty"], "OEM Warranty", "Üretici Garantisi (OEM)"),
        (lambda: dict(ContractTypeChoices)["vendor_support"], "Vendor Support / Care Pack",
         "Üretici Destek Paketi (Care Pack)"),
        (lambda: dict(ContractTypeChoices)["tpm"], "Third-Party Maintenance (TPM)", "3. Parti Bakım Anlaşması (TPM)"),
        (lambda: dict(ContractTypeChoices)["software"], "Software License / Subscription",
         "Yazılım Lisansı / Abonelik"),
        (lambda: SupportContract._meta.get_field("contract_type").verbose_name, "contract type", "sözleşme türü"),
        (lambda: SupportCoverage._meta.get_field("quantity").verbose_name, "quantity", "adet / miktar"),
        (lambda: SupportCoverage._meta.get_field("quantity").help_text,
         "License count, seats, sockets, or covered units (optional).",
         "Lisans adedi, kullanıcı, soket veya kapsanan birim sayısı (isteğe bağlı)."),
        (lambda: SupportContractTable.base_columns["contract_type"].verbose_name, "Type", "Tür"),
        (lambda: SupportContractTable.base_columns["sku_count"].verbose_name, "Licenses / SKUs",
         "Lisans / SKU Kalemleri"),
        (lambda: SupportCoverageTable.base_columns["contract_type"].verbose_name, "Contract Type", "Sözleşme Türü"),
        (lambda: SupportCoverageBulkEditForm.base_fields["contract"].help_text,
         "Move selected coverage items to another contract",
         "Seçilen kapsam kayıtlarını başka bir sözleşmeye taşıyın"),
    )

    def test_strings(self):
        for getter, english, turkish in self.STRINGS:
            for language, expected in (("tr", turkish), ("en", english), ("tr", turkish)):
                with self.subTest(expected=expected), translation.override(language):
                    self.assertIn(expected, str(getter()))

    def test_device_panel_in_turkish(self):
        self.add_permissions(f"{PLUGIN}.view_supportcoverage", "dcim.view_device")
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "tr"
        response = self.client.get(self.storage_a.get_absolute_url())
        self.assertContains(response, "Bayi / Destek Sağlayıcı")
        self.assertContains(response, "3. Parti Bakım Anlaşması (TPM)")
