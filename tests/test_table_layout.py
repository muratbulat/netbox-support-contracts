from django.utils import translation
from utilities.testing import TestCase

from netbox_support_contracts.choices import ContractTypeChoices
from netbox_support_contracts.models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from netbox_support_contracts.services import annotate_contracts, annotate_coverage, annotate_devices
from netbox_support_contracts.tables import (
    DeviceCoverageTable,
    HardwareLifecycleEoLTable,
    SupportContractTable,
    SupportCoverageTable,
)

from . import cover, days, make_contract, make_device, make_device_type, make_integrator, make_maintenance_company

NOWRAP_COLUMNS = {
    SupportContractTable: ("start_date", "end_date", "state", "days_remaining"),
    SupportCoverageTable: ("start_date", "effective_end_date", "end_date", "state", "days_remaining"),
    HardwareLifecycleEoLTable: ("end_of_sale", "end_of_maintenance", "end_of_support", "state", "days_remaining"),
    DeviceCoverageTable: ("end_of_support", "coverage", "coverage_end", "days_remaining"),
}


class DefaultColumnTests(TestCase):
    def test_contract_defaults(self):
        self.assertEqual(
            SupportContractTable.Meta.default_columns,
            (
                "pk", "contract_number", "contract_type", "manufacturer", "integrator", "maintenance_company",
                "state", "end_date", "days_remaining", "device_count", "actions",
            ),
        )

    def test_coverage_defaults(self):
        self.assertEqual(
            SupportCoverageTable.Meta.default_columns,
            (
                "pk", "contract", "contract_type", "device", "device_serial", "device_type", "site",
                "effective_end_date", "state", "days_remaining", "actions",
            ),
        )

    def test_secondary_columns_stay_configurable(self):
        contract_optional = {
            "start_date", "service_level", "description", "vm_count", "module_count", "sku_count", "cost_display",
            "portal_url", "tenant", "comments", "tags", "created", "last_updated",
        }
        coverage_optional = {
            "manufacturer", "integrator", "maintenance_company", "virtual_machine", "module", "sku", "quantity",
            "start_date", "end_date", "notes", "tags",
        }
        for table, optional in ((SupportContractTable, contract_optional), (SupportCoverageTable, coverage_optional)):
            with self.subTest(table=table.__name__):
                self.assertTrue(optional <= set(table.Meta.fields))
                self.assertFalse(optional & set(table.Meta.default_columns))
                self.assertTrue(set(table.Meta.default_columns) <= set(table.Meta.fields))

    def test_concise_headers(self):
        for table in (SupportContractTable, SupportCoverageTable):
            for language, integrator, company in (("en", "Integrator", "Maintenance Company"),
                                                  ("tr", "Entegratör", "Bakım Firması")):
                with self.subTest(table=table.__name__, language=language), translation.override(language):
                    self.assertEqual(str(table.base_columns["integrator"].verbose_name), integrator)
                    self.assertEqual(str(table.base_columns["maintenance_company"].verbose_name), company)


class NoWrapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        device_type = make_device_type("Storage-Array")
        cls.device = make_device("Device-01", device_type=device_type)
        contract = make_contract(
            "CONT-2026-001", start_date=days(-300), end_date=days(30), contract_type=ContractTypeChoices.TPM,
            integrator=make_integrator("Acme Corp"), maintenance_company=make_maintenance_company("Example TPM"),
        )
        cover(contract, cls.device)
        HardwareLifecycleEoL.objects.create(
            device_type=device_type, end_of_sale=days(-400), end_of_maintenance=days(-100), end_of_support=days(200)
        )

    def test_date_and_badge_cells_do_not_wrap(self):
        from dcim.models import Device

        querysets = {
            SupportContractTable: annotate_contracts(SupportContract.objects.all()),
            SupportCoverageTable: annotate_coverage(SupportCoverage.objects.all()),
            HardwareLifecycleEoLTable: HardwareLifecycleEoL.objects.all(),
            DeviceCoverageTable: annotate_devices(Device.objects.filter(pk=self.device.pk)),
        }
        for table_class, names in NOWRAP_COLUMNS.items():
            table = table_class(querysets[table_class])
            row = table.rows[0]
            for name in names:
                with self.subTest(table=table_class.__name__, column=name):
                    self.assertIn("text-nowrap", table.columns[name].attrs["td"]["class"])
                    self.assertTrue(str(row.get_cell(name)))

    def test_coverage_contract_type_badge_matches_contract_table_color(self):
        for idx, (choice_val, _, color) in enumerate(ContractTypeChoices.CHOICES, start=10):
            contract = make_contract(
                f"CONT-COLOR-{idx}",
                start_date=days(-30),
                end_date=days(30),
                contract_type=choice_val,
                maintenance_company=make_maintenance_company(f"TPM-{idx}"),
            )
            cov = cover(contract, make_device(f"Color-Dev-{idx}"))
            contract_table = SupportContractTable(annotate_contracts(SupportContract.objects.filter(pk=contract.pk)))
            coverage_table = SupportCoverageTable(annotate_coverage(SupportCoverage.objects.filter(pk=cov.pk)))
            contract_cell = str(contract_table.rows[0].get_cell("contract_type"))
            coverage_cell = str(coverage_table.rows[0].get_cell("contract_type"))
            self.assertEqual(coverage_cell, contract_cell)
            self.assertIn(f"text-bg-{color}", coverage_cell)

