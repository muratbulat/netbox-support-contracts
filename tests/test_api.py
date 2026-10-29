import json

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from utilities.testing import APITestCase

from netbox_support_contracts.models import SupportContract, SupportCoverage
from netbox_support_contracts.services import VIEW_COST_PERMISSION

from . import cover, days, make_contract, make_device, make_manufacturer, make_vm

PLUGIN = "netbox_support_contracts"


def api_url(name, *args):
    return reverse(f"plugins-api:{PLUGIN}-api:{name}", args=args)


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class APITestBase(APITestCase):
    model = SupportContract

    @classmethod
    def setUpTestData(cls):
        cls.manufacturer = make_manufacturer()
        cls.contract = make_contract(
            "CONT-2026-001", end_date=days(45), service_level="8x5xNBD", cost="1200.00", currency="USD"
        )
        cls.device_1 = make_device("Device-01", serial="SN-10001")
        cls.device_2 = make_device("Device-02", serial="SN-10002")
        cls.device_3 = make_device("Device-03", serial="SN-DUP")
        cls.device_4 = make_device("Device-04", serial="sn-dup")
        cls.coverage = cover(cls.contract, cls.device_1)

    def get(self, url, **params):
        return self.client.get(url, params, **self.header)

    def post(self, url, data):
        return self.client.post(url, data, format="json", **self.header)


class SupportContractAPITests(APITestBase):
    def test_list_requires_permission(self):
        self.assertHttpStatus(self.get(api_url("supportcontract-list")), status.HTTP_403_FORBIDDEN)

    def test_computed_fields(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract")
        response = self.get(api_url("supportcontract-detail", self.contract.pk))
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["state"], "expiring")
        self.assertEqual(response.data["days_remaining"], 45)
        self.assertEqual(response.data["device_count"], 1)
        self.assertEqual(response.data["vm_count"], 0)

    def test_cost_hidden_without_permission(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract")
        response = self.get(api_url("supportcontract-list"))
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertNotIn("cost", response.data["results"][0])
        self.assertNotIn("currency", response.data["results"][0])

    def test_cost_visible_with_permission(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", VIEW_COST_PERMISSION)
        response = self.get(api_url("supportcontract-detail", self.contract.pk))
        self.assertEqual(str(response.data["cost"]), "1200.00")
        self.assertEqual(response.data["currency"], "USD")

    def test_cannot_write_cost_without_permission(self):
        self.add_permissions(f"{PLUGIN}.add_supportcontract", "dcim.view_manufacturer")
        data = {"manufacturer": self.manufacturer.pk, "contract_number": "CONT-2026-002", "cost": "10.00"}
        response = self.post(api_url("supportcontract-list"), data)
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cost", response.data)
        del data["cost"]
        response = self.post(api_url("supportcontract-list"), data)
        self.assertHttpStatus(response, status.HTTP_201_CREATED)

    def test_create_with_cost_permission(self):
        self.add_permissions(f"{PLUGIN}.add_supportcontract", VIEW_COST_PERMISSION, "dcim.view_manufacturer")
        response = self.post(api_url("supportcontract-list"), {
            "manufacturer": self.manufacturer.pk,
            "contract_number": "CONT-2026-003",
            "end_date": days(400).isoformat(),
            "cost": "99.50",
            "currency": "eur",
        })
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertEqual(SupportContract.objects.get(contract_number="CONT-2026-003").currency, "EUR")
        self.assertEqual(response.data["state"], "active")

    def test_validation_errors(self):
        self.add_permissions(f"{PLUGIN}.add_supportcontract", "dcim.view_manufacturer")
        response = self.post(api_url("supportcontract-list"), {
            "manufacturer": self.manufacturer.pk,
            "contract_number": "CONT-2026-004",
            "start_date": days(10).isoformat(),
            "end_date": days(1).isoformat(),
        })
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)

    def test_state_filter(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract")
        make_contract("CONT-2026-005", end_date=None)
        response = self.get(api_url("supportcontract-list"), state="active")
        self.assertEqual([row["contract_number"] for row in response.data["results"]], ["CONT-2026-005"])

    def test_summary(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", "dcim.view_device")
        response = self.get(api_url("supportcontract-summary"))
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["devices"]["total"], 4)
        self.assertEqual(response.data["devices"]["expiring"], 1)
        self.assertEqual(response.data["devices"]["none"], 3)
        self.assertEqual(response.data["soonest_contracts"][0]["contract_number"], "CONT-2026-001")
        self.assertEqual(response.data["soonest_contracts"][0]["days_remaining"], 45)

    def test_summary_requires_permission(self):
        self.assertHttpStatus(self.get(api_url("supportcontract-summary")), status.HTTP_403_FORBIDDEN)


class SupportCoverageAPITests(APITestBase):
    model = SupportCoverage

    def test_coverage_fields(self):
        self.add_permissions(f"{PLUGIN}.view_supportcoverage")
        response = self.get(api_url("supportcoverage-detail", self.coverage.pk))
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["effective_end_date"], days(45).isoformat())
        self.assertEqual(response.data["state"], "expiring")
        self.assertEqual(response.data["device"]["id"], self.device_1.pk)
        self.assertNotIn("cost", response.data["contract"])

    def test_bulk_add(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcontract", "dcim.view_device")
        response = self.post(api_url("supportcoverage-bulk-add"), {
            "contract_id": self.contract.pk,
            "device_ids": [self.device_1.pk],
            "serials": "sn-10002\nSN-DUP, SN-MISSING",
            "sku": "SUP-8X5-NBD",
        })
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["created"]), 1)
        self.assertEqual(response.data["existing"][0]["id"], self.device_1.pk)
        self.assertEqual([d["id"] for d in response.data["matched"]], [self.device_2.pk])
        self.assertEqual(response.data["unmatched"], ["SN-MISSING"])
        self.assertEqual(response.data["ambiguous"], ["SN-DUP"])
        self.assertTrue(SupportCoverage.objects.filter(device=self.device_2, sku="SUP-8X5-NBD").exists())

    def test_bulk_add_serial_list_and_vm(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcontract", "dcim.view_device",
            "virtualization.view_virtualmachine",
        )
        vm = make_vm()
        response = self.post(api_url("supportcoverage-bulk-add"), {
            "contract_id": self.contract.pk, "serials": ["SN-10002"], "virtual_machine_ids": [vm.pk],
        })
        self.assertHttpStatus(response, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["created"]), 2)

    def test_bulk_add_nothing_new_returns_200(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcontract", "dcim.view_device")
        response = self.post(api_url("supportcoverage-bulk-add"), {
            "contract_id": self.contract.pk, "device_ids": [self.device_1.pk],
        })
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["created"], [])

    def test_bulk_add_rejects_invisible_ids(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcontract")
        response = self.post(api_url("supportcoverage-bulk-add"), {
            "contract_id": self.contract.pk, "device_ids": [self.device_2.pk],
        })
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn("device_ids", response.data)

    def test_bulk_add_requires_add_permission(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", "dcim.view_device")
        response = self.post(api_url("supportcoverage-bulk-add"), {
            "contract_id": self.contract.pk, "device_ids": [self.device_2.pk],
        })
        self.assertHttpStatus(response, status.HTTP_403_FORBIDDEN)

    def test_bulk_add_validation(self):
        self.add_permissions(f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcontract")
        response = self.post(api_url("supportcoverage-bulk-add"), {"contract_id": self.contract.pk})
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        response = self.post(api_url("supportcoverage-bulk-add"), {"contract_id": 999999, "serials": "SN-10001"})
        self.assertHttpStatus(response, status.HTTP_400_BAD_REQUEST)
        self.assertIn("contract_id", response.data)

    def test_bulk_remove_requires_delete_permission(self):
        self.add_permissions(
            f"{PLUGIN}.add_supportcoverage", f"{PLUGIN}.view_supportcoverage", f"{PLUGIN}.view_supportcontract"
        )
        data = {"contract_id": self.contract.pk, "device_ids": [self.device_1.pk]}
        self.assertHttpStatus(self.post(api_url("supportcoverage-bulk-remove"), data), status.HTTP_403_FORBIDDEN)
        self.add_permissions(f"{PLUGIN}.delete_supportcoverage")
        response = self.post(api_url("supportcoverage-bulk-remove"), data)
        self.assertHttpStatus(response, status.HTTP_200_OK)
        self.assertEqual(response.data["removed"], 1)
        self.assertFalse(SupportCoverage.objects.filter(pk=self.coverage.pk).exists())


class GraphQLTests(APITestBase):
    QUERY = "{ support_contract_list { contract_number state days_remaining cost currency coverage { id } } }"

    def query(self):
        response = self.client.post(
            reverse("graphql"), data=json.dumps({"query": self.QUERY}), content_type="application/json", **self.header
        )
        self.assertHttpStatus(response, status.HTTP_200_OK)
        payload = json.loads(response.content)
        self.assertNotIn("errors", payload, payload.get("errors"))
        return payload["data"]["support_contract_list"]

    def test_cost_is_null_without_permission(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage")
        rows = self.query()
        self.assertEqual(rows[0]["contract_number"], "CONT-2026-001")
        self.assertEqual(rows[0]["state"], "expiring")
        self.assertEqual(rows[0]["days_remaining"], 45)
        self.assertIsNone(rows[0]["cost"])
        self.assertIsNone(rows[0]["currency"])
        self.assertEqual(len(rows[0]["coverage"]), 1)

    def test_coverage_and_lifecycle_queries(self):
        from netbox_support_contracts.models import HardwareLifecycleEoL

        HardwareLifecycleEoL.objects.create(device_type=self.device_1.device_type, end_of_support=days(-1))
        self.add_permissions(
            f"{PLUGIN}.view_supportcoverage", f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_hardwarelifecycleeol"
        )
        self.QUERY = (
            "{ support_coverage_list { effective_end_date state days_remaining contract { contract_number } } "
            "hardware_lifecycle_eol_list { end_of_support state days_remaining } }"
        )
        response = self.client.post(
            reverse("graphql"), data=json.dumps({"query": self.QUERY}), content_type="application/json", **self.header
        )
        payload = json.loads(response.content)
        self.assertNotIn("errors", payload, payload.get("errors"))
        coverage = payload["data"]["support_coverage_list"][0]
        self.assertEqual(coverage["effective_end_date"], days(45).isoformat())
        self.assertEqual(coverage["state"], "expiring")
        self.assertEqual(payload["data"]["hardware_lifecycle_eol_list"][0]["state"], "expired")

    def test_cost_visible_with_permission(self):
        self.add_permissions(f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage", VIEW_COST_PERMISSION)
        rows = self.query()
        self.assertEqual(rows[0]["cost"], "1200.00")
        self.assertEqual(rows[0]["currency"], "USD")
