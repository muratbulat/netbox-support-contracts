from core.models import ObjectType
from dcim.models import Device
from django.core.exceptions import ValidationError
from django.test import TestCase
from users.models import ObjectPermission, User
from utilities.exceptions import PermissionsViolation

from netbox_support_contracts.models import SupportCoverage
from netbox_support_contracts.services import (
    MAX_BULK_ITEMS,
    bulk_add_coverage,
    bulk_remove_coverage,
    match_serials,
    parse_serials,
)

from . import cover, make_contract, make_device, make_module, make_vm


def grant(user, model, actions, constraints=None):
    permission = ObjectPermission.objects.create(
        name=f"{model._meta.model_name}-{'-'.join(actions)}", actions=actions, constraints=constraints
    )
    permission.object_types.add(ObjectType.objects.get_for_model(model))
    permission.users.add(user)


class ParseSerialsTests(TestCase):
    def test_separators(self):
        text = "SN-10001\nSN-10002,SN-10003;SN-10004 SN-10005\tSN-10006\r\nSN-10007"
        self.assertEqual(parse_serials(text), [f"SN-1000{i}" for i in range(1, 8)])

    def test_case_insensitive_dedupe_preserves_first_spelling_and_order(self):
        self.assertEqual(parse_serials("sn-b, SN-A ,SN-B; sn-a\nSN-C"), ["sn-b", "SN-A", "SN-C"])

    def test_blank_input(self):
        self.assertEqual(parse_serials(""), [])
        self.assertEqual(parse_serials(None), [])
        self.assertEqual(parse_serials(" ,;\n\n "), [])

    def test_spreadsheet_column_paste(self):
        self.assertEqual(parse_serials("SN-10001\r\nSN-10002\r\n\r\n"), ["SN-10001", "SN-10002"])


class MatchSerialsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.device_1 = make_device("Device-01", serial="SN-10001")
        cls.device_2 = make_device("Device-02", serial="sn-10002")
        cls.twin_a = make_device("Device-03", serial="SN-20000")
        cls.twin_b = make_device("Device-04", serial="sn-20000")
        make_device("Device-05", serial="")

    def test_exact_and_case_insensitive_matches(self):
        result = match_serials(Device.objects.all(), ["SN-10001", "SN-10002"])
        self.assertEqual(result["devices"], [self.device_1, self.device_2])
        self.assertEqual(result["unmatched"], [])
        self.assertEqual(result["ambiguous"], [])

    def test_unmatched_serials_keep_original_spelling(self):
        result = match_serials(Device.objects.all(), ["sn-99999", "SN-10001"])
        self.assertEqual(result["devices"], [self.device_1])
        self.assertEqual(result["unmatched"], ["sn-99999"])

    def test_ambiguous_serial_is_never_guessed(self):
        result = match_serials(Device.objects.all(), ["SN-20000"])
        self.assertEqual(result, {"devices": [], "unmatched": [], "ambiguous": ["SN-20000"]})

    def test_match_respects_queryset_scope(self):
        result = match_serials(Device.objects.exclude(pk=self.twin_b.pk), ["SN-20000"])
        self.assertEqual(result["devices"], [self.twin_a])

    def test_single_query(self):
        serials = [f"SN-{n:05d}" for n in range(1000)]
        with self.assertNumQueries(1):
            match_serials(Device.objects.all(), serials)

    def test_empty_serial_list(self):
        with self.assertNumQueries(0):
            self.assertEqual(match_serials(Device.objects.all(), []), {"devices": [], "unmatched": [], "ambiguous": []})


class BulkAddCoverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="admin", is_superuser=True)
        cls.contract = make_contract()
        cls.device_1 = make_device("Device-01", serial="SN-10001")
        cls.device_2 = make_device("Device-02", serial="SN-10002")
        cls.device_3 = make_device("Device-03", serial="SN-10003")
        make_device("Device-04", serial="SN-DUP")
        make_device("Device-05", serial="sn-dup")
        cls.vm = make_vm()
        cls.module = make_module(cls.device_1)

    def test_serials_and_picked_objects(self):
        cover(self.contract, self.device_1)
        result = bulk_add_coverage(
            self.contract,
            user=self.admin,
            devices=[self.device_2],
            virtual_machines=[self.vm],
            modules=[self.module],
            serials=parse_serials("sn-10001\nSN-10002\nSN-10003\nSN-MISSING\nSN-DUP"),
            sku="SUP-8X5-NBD",
        )
        self.assertEqual(result.matched, [self.device_1, self.device_2, self.device_3])
        self.assertEqual(result.unmatched, ["SN-MISSING"])
        self.assertEqual(result.ambiguous, ["SN-DUP"])
        self.assertEqual(result.existing, [self.device_1])
        self.assertEqual(
            sorted(str(row.covered_object) for row in result.created),
            sorted([str(self.device_2), str(self.device_3), str(self.vm), str(self.module)]),
        )
        self.assertTrue(result.needs_review)
        self.assertEqual(SupportCoverage.objects.filter(contract=self.contract).count(), 5)
        self.assertTrue(all(row.sku == "SUP-8X5-NBD" for row in result.created))

    def test_rerun_is_idempotent(self):
        bulk_add_coverage(self.contract, user=self.admin, devices=[self.device_1, self.device_2])
        result = bulk_add_coverage(self.contract, user=self.admin, devices=[self.device_1, self.device_2])
        self.assertEqual(result.created, [])
        self.assertEqual(result.existing, [self.device_1, self.device_2])
        self.assertFalse(result.needs_review)

    def test_device_picked_and_matched_by_serial_is_added_once(self):
        result = bulk_add_coverage(self.contract, user=self.admin, devices=[self.device_1], serials=["SN-10001"])
        self.assertEqual(len(result.created), 1)

    def test_serial_limit(self):
        with self.assertRaises(ValidationError):
            bulk_add_coverage(
                self.contract, user=self.admin, serials=[f"SN-{n}" for n in range(MAX_BULK_ITEMS + 1)]
            )

    def test_serials_only_match_devices_the_user_may_view(self):
        user = User.objects.create_user(username="operator")
        grant(user, Device, ["view"], constraints={"name": "Device-01"})
        grant(user, SupportCoverage, ["add"])
        result = bulk_add_coverage(self.contract, user=user, serials=["SN-10001", "SN-10002"])
        self.assertEqual(result.matched, [self.device_1])
        self.assertEqual(result.unmatched, ["SN-10002"])

    def test_constraint_violation_rolls_back_everything(self):
        user = User.objects.create_user(username="operator")
        grant(user, Device, ["view"])
        grant(user, SupportCoverage, ["add"], constraints={"device__name": "Device-01"})
        with self.assertRaises(PermissionsViolation):
            bulk_add_coverage(self.contract, user=user, devices=[self.device_1, self.device_2])
        self.assertFalse(SupportCoverage.objects.exists())


class BulkRemoveCoverageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="admin", is_superuser=True)
        cls.contract = make_contract()
        cls.other = make_contract("CONT-2026-002")
        cls.device_1 = make_device("Device-01")
        cls.device_2 = make_device("Device-02")
        cls.row_1 = cover(cls.contract, cls.device_1)
        cls.row_2 = cover(cls.contract, cls.device_2)
        cls.other_row = cover(cls.other, cls.device_1)

    def test_remove_by_device_only_touches_this_contract(self):
        removed = bulk_remove_coverage(self.contract, user=self.admin, device_ids=[self.device_1.pk])
        self.assertEqual(removed, 1)
        self.assertTrue(SupportCoverage.objects.filter(pk=self.other_row.pk).exists())
        self.assertTrue(SupportCoverage.objects.filter(pk=self.row_2.pk).exists())

    def test_remove_by_row_id_ignores_other_contracts(self):
        removed = bulk_remove_coverage(self.contract, user=self.admin, coverage_ids=[self.row_2.pk, self.other_row.pk])
        self.assertEqual(removed, 1)
        self.assertTrue(SupportCoverage.objects.filter(pk=self.other_row.pk).exists())

    def test_delete_constraint_violation_removes_nothing(self):
        user = User.objects.create_user(username="operator")
        grant(user, SupportCoverage, ["view"])
        grant(user, SupportCoverage, ["delete"], constraints={"device__name": "Device-01"})
        with self.assertRaises(PermissionsViolation):
            bulk_remove_coverage(self.contract, user=user, coverage_ids=[self.row_1.pk, self.row_2.pk])
        self.assertEqual(SupportCoverage.objects.filter(contract=self.contract).count(), 2)
