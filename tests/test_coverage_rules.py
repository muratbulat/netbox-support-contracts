from datetime import date

from core.models import ObjectType
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from extras.models import Notification, Subscription
from users.models import User

from netbox_support_contracts.choices import ContractStateChoices, CoverageStatusChoices
from netbox_support_contracts.jobs import CONTRACT_EXPIRED, CONTRACT_EXPIRING, process_contract_expiry
from netbox_support_contracts.models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from netbox_support_contracts.services import (
    OPEN_ENDED,
    annotate_contracts,
    annotate_devices,
    contract_state,
    contract_state_q,
    coverage_status,
    coverage_summary,
    days_until,
    lifecycle_flags,
    object_coverage,
)

from . import cover, days, make_contract, make_device, make_device_type, make_manufacturer, make_module, make_vm

TODAY = date(2026, 6, 15)


class ContractStateTests(TestCase):
    def test_open_ended_is_active(self):
        self.assertEqual(contract_state(None, 90, TODAY), ContractStateChoices.ACTIVE)
        self.assertIsNone(days_until(None, TODAY))

    def test_active_beyond_warning_window(self):
        self.assertEqual(contract_state(date(2026, 9, 14), 90, TODAY), ContractStateChoices.ACTIVE)  # 91 days

    def test_expiring_within_warning_window_inclusive(self):
        self.assertEqual(contract_state(date(2026, 9, 13), 90, TODAY), ContractStateChoices.EXPIRING)  # 90 days
        self.assertEqual(contract_state(date(2026, 6, 16), 90, TODAY), ContractStateChoices.EXPIRING)

    def test_end_date_is_last_covered_day(self):
        self.assertEqual(contract_state(TODAY, 90, TODAY), ContractStateChoices.EXPIRING)
        self.assertEqual(days_until(TODAY, TODAY), 0)

    def test_expired_after_end_date(self):
        self.assertEqual(contract_state(date(2026, 6, 14), 90, TODAY), ContractStateChoices.EXPIRED)
        self.assertEqual(days_until(date(2026, 6, 14), TODAY), -1)

    def test_zero_warning_days(self):
        self.assertEqual(contract_state(date(2026, 6, 16), 0, TODAY), ContractStateChoices.ACTIVE)
        self.assertEqual(contract_state(TODAY, 0, TODAY), ContractStateChoices.EXPIRING)


class CoverageStatusTests(TestCase):
    def test_no_coverage(self):
        self.assertEqual(coverage_status(None, 90, TODAY), CoverageStatusChoices.NONE)

    def test_open_ended_coverage(self):
        self.assertEqual(coverage_status(OPEN_ENDED, 90, TODAY), CoverageStatusChoices.COVERED)

    def test_covered_expiring_expired(self):
        self.assertEqual(coverage_status(date(2027, 6, 15), 90, TODAY), CoverageStatusChoices.COVERED)
        self.assertEqual(coverage_status(date(2026, 7, 1), 90, TODAY), CoverageStatusChoices.EXPIRING)
        self.assertEqual(coverage_status(date(2026, 1, 1), 90, TODAY), CoverageStatusChoices.EXPIRED)


class StateQueryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.open_ended = make_contract("CONT-2026-001", end_date=None)
        cls.active = make_contract("CONT-2026-002", end_date=days(200))
        cls.expiring = make_contract("CONT-2026-003", end_date=days(30))
        cls.ends_today = make_contract("CONT-2026-004", end_date=days(0))
        cls.expired = make_contract("CONT-2026-005", end_date=days(-1))

    def select(self, state):
        return set(SupportContract.objects.filter(contract_state_q(state, 90)).values_list("pk", flat=True))

    def test_sql_state_matches_python_state(self):
        for contract in SupportContract.objects.all():
            with self.subTest(contract=contract.contract_number):
                self.assertIn(contract.pk, self.select(contract.state))
        self.assertEqual(self.select(ContractStateChoices.ACTIVE), {self.open_ended.pk, self.active.pk})
        self.assertEqual(self.select(ContractStateChoices.EXPIRING), {self.expiring.pk, self.ends_today.pk})
        self.assertEqual(self.select(ContractStateChoices.EXPIRED), {self.expired.pk})

    def test_model_properties_never_stale(self):
        self.assertEqual(self.expiring.days_remaining, 30)
        self.assertEqual(self.ends_today.state, ContractStateChoices.EXPIRING)
        self.assertEqual(self.open_ended.state, ContractStateChoices.ACTIVE)
        self.assertIsNone(self.open_ended.days_remaining)

    def test_open_ended_sorts_last(self):
        ordered = annotate_contracts(SupportContract.objects.all()).order_by("effective_sort_end")
        self.assertEqual(ordered.last(), self.open_ended)

    @override_settings(PLUGINS_CONFIG={"netbox_support_contracts": {"warning_days": 10}})
    def test_warning_days_setting(self):
        self.assertEqual(self.expiring.state, ContractStateChoices.ACTIVE)


class AnnotationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.device_a = make_device("Device-01", serial="SN-10001")
        cls.device_b = make_device("Device-02", serial="SN-10002")
        cls.device_c = make_device("Device-03", serial="SN-10003")
        cls.device_d = make_device("Device-04", serial="SN-10004")
        cls.vm = make_vm()
        cls.module = make_module(cls.device_a)
        cls.short = make_contract("CONT-2026-001", end_date=days(30))
        cls.long = make_contract("CONT-2026-002", end_date=days(400))
        cls.open = make_contract("CONT-2026-003", end_date=None)
        cls.lapsed = make_contract("CONT-2026-004", end_date=days(-10))
        cover(cls.short, cls.device_a)
        cover(cls.long, cls.device_a)
        cover(cls.short, cls.device_b, end_date=days(-5))  # per-item override wins over the contract end
        cover(cls.open, cls.device_c)
        cover(cls.lapsed, cls.device_d)
        cover(cls.short, cls.vm)
        cover(cls.short, cls.module)

    def test_annotate_contracts_counts(self):
        short = annotate_contracts(SupportContract.objects.filter(pk=self.short.pk)).get()
        self.assertEqual((short.device_count, short.vm_count, short.module_count), (2, 1, 1))
        lapsed = annotate_contracts(SupportContract.objects.filter(pk=self.lapsed.pk)).get()
        self.assertEqual((lapsed.device_count, lapsed.vm_count, lapsed.module_count), (1, 0, 0))

    def test_annotate_devices(self):
        from dcim.models import Device

        rows = {d.pk: d for d in annotate_devices(Device.objects.all())}
        self.assertEqual(rows[self.device_a.pk].coverage_end_annotated, days(400))
        self.assertEqual(rows[self.device_a.pk].coverage_contracts_annotated, 2)
        self.assertEqual(rows[self.device_b.pk].coverage_end_annotated, days(-5))
        self.assertEqual(rows[self.device_c.pk].coverage_end_annotated, OPEN_ENDED)
        self.assertEqual(rows[self.device_d.pk].coverage_end_annotated, days(-10))

    def test_annotate_devices_without_coverage(self):
        from dcim.models import Device

        uncovered = make_device("Device-05")
        row = annotate_devices(Device.objects.filter(pk=uncovered.pk)).get()
        self.assertIsNone(row.coverage_end_annotated)
        self.assertEqual(row.coverage_contracts_annotated, 0)

    def test_annotate_virtual_machines(self):
        from virtualization.models import VirtualMachine

        row = annotate_devices(VirtualMachine.objects.filter(pk=self.vm.pk)).get()
        self.assertEqual(row.coverage_end_annotated, days(30))

    def test_coverage_summary(self):
        make_device("Device-05")
        summary = coverage_summary()
        self.assertEqual(
            summary["devices"],
            {"total": 5, "covered": 2, "expiring": 0, "expired": 2, "none": 1},
        )
        self.assertEqual([c.pk for c in summary["soonest_contracts"]], [self.short.pk, self.long.pk])

    def test_object_coverage(self):
        coverage = object_coverage(self.device_a)
        self.assertEqual(coverage["status"], CoverageStatusChoices.COVERED)
        self.assertEqual(coverage["end_date"], days(400))
        self.assertEqual(coverage["days_remaining"], 400)
        self.assertEqual(len(coverage["rows"]), 2)
        self.assertEqual(object_coverage(self.device_c)["open_ended"], True)
        self.assertEqual(object_coverage(make_device("Device-06"))["status"], CoverageStatusChoices.NONE)


class LifecycleFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.device_type = make_device_type("Router-EoL")
        cls.device = make_device("Device-01", device_type=cls.device_type)

    def set_eos(self, eos):
        HardwareLifecycleEoL.objects.update_or_create(device_type=self.device_type, defaults={"end_of_support": eos})
        self.device.device_type.refresh_from_db()

    def codes(self, *args):
        return [flag["code"] for flag in lifecycle_flags(self.device, *args, today=TODAY)]

    def test_no_lifecycle_record(self):
        self.assertEqual(lifecycle_flags(self.device, date(2030, 1, 1), True, TODAY), [])

    def test_eol_under_contract(self):
        self.set_eos(date(2026, 1, 1))
        self.assertEqual(self.codes(date(2025, 12, 1), True), ["eol_under_contract"])
        self.assertEqual(self.codes(date(2025, 12, 1), False), [])

    def test_eos_today_counts_as_eol(self):
        self.set_eos(TODAY)
        self.assertIn("eol_under_contract", self.codes(None, True))

    def test_contract_after_eol(self):
        self.set_eos(date(2027, 1, 1))
        self.assertEqual(self.codes(date(2028, 1, 1), True), ["contract_after_eol"])
        self.assertEqual(self.codes(date(2027, 1, 1), True), [])
        self.assertEqual(self.codes(None, True), [])

    def test_both_flags(self):
        self.set_eos(date(2026, 1, 1))
        self.assertEqual(self.codes(date(2026, 12, 31), True), ["eol_under_contract", "contract_after_eol"])

    def test_virtual_machine_has_no_flags(self):
        self.assertEqual(lifecycle_flags(make_vm(), date(2030, 1, 1), True, TODAY), [])

    def test_object_coverage_reports_flags(self):
        self.set_eos(days(-30))
        cover(make_contract("CONT-2026-001", end_date=days(100)), self.device)
        codes = [flag["code"] for flag in object_coverage(self.device)["flags"]]
        self.assertEqual(codes, ["eol_under_contract", "contract_after_eol"])


class ModelValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manufacturer = make_manufacturer()
        cls.contract = make_contract(end_date=days(100))
        cls.device = make_device("Device-01")

    def test_end_before_start(self):
        contract = SupportContract(
            manufacturer=self.manufacturer, contract_number="CONT-2026-009", start_date=days(10), end_date=days(1)
        )
        with self.assertRaises(ValidationError) as cm:
            contract.full_clean()
        self.assertIn("end_date", cm.exception.message_dict)

    def test_cost_requires_currency(self):
        contract = SupportContract(
            manufacturer=self.manufacturer, contract_number="CONT-2026-009", cost="1200.00", currency=""
        )
        with self.assertRaises(ValidationError) as cm:
            contract.full_clean()
        self.assertIn("currency", cm.exception.message_dict)

    def test_currency_normalized(self):
        contract = SupportContract(
            manufacturer=self.manufacturer, contract_number="CONT-2026-009", cost="1200.00", currency="eur"
        )
        contract.full_clean()
        contract.save()
        self.assertEqual(contract.currency, "EUR")
        self.assertEqual(contract.cost_display, "1,200.00 EUR")

    def test_contract_number_unique_per_manufacturer_case_insensitive(self):
        duplicate = SupportContract(manufacturer=self.manufacturer, contract_number="cont-2026-001")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        other = SupportContract(manufacturer=make_manufacturer("Example Networks"), contract_number="CONT-2026-001")
        other.full_clean()

    def test_coverage_requires_exactly_one_object_or_sku(self):
        with self.assertRaises(ValidationError) as cm:
            SupportCoverage(contract=self.contract).full_clean()
        self.assertIn("device", cm.exception.message_dict)
        SupportCoverage(contract=self.contract, sku="LIC-SW-0001").full_clean()
        with self.assertRaises(ValidationError) as cm:
            SupportCoverage(contract=self.contract, device=self.device, virtual_machine=make_vm()).full_clean()
        self.assertEqual(set(cm.exception.message_dict), {"device", "virtual_machine"})

    def test_duplicate_coverage_is_field_error(self):
        cover(self.contract, self.device)
        with self.assertRaises(ValidationError) as cm:
            SupportCoverage(contract=self.contract, device=self.device).full_clean()
        self.assertEqual(list(cm.exception.message_dict), ["device"])

    def test_effective_end_date_override(self):
        row = cover(self.contract, self.device, end_date=days(5))
        self.assertEqual(row.effective_end_date, days(5))
        self.assertEqual(row.state, ContractStateChoices.EXPIRING)
        row.end_date = None
        self.assertEqual(row.effective_end_date, days(100))
        self.assertEqual(row.covered_object, self.device)

    def test_eol_requires_exactly_one_type(self):
        with self.assertRaises(ValidationError):
            HardwareLifecycleEoL(end_of_support=days(1)).full_clean()
        eol = HardwareLifecycleEoL(
            device_type=make_device_type(), end_of_sale=days(10), end_of_support=days(5)
        )
        with self.assertRaises(ValidationError) as cm:
            eol.full_clean()
        self.assertIn("end_of_support", cm.exception.message_dict)


class ExpiryJobTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="subscriber")
        cls.contract = make_contract(end_date=days(200))
        Subscription.objects.create(user=cls.user, object=cls.contract)

    def notifications(self):
        return list(Notification.objects.filter(user=self.user).values_list("event_type", flat=True))

    def test_transitions_are_announced_once(self):
        self.assertEqual(process_contract_expiry(), {"expiring": 0, "expired": 0})
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.notified_state, ContractStateChoices.ACTIVE)
        self.assertEqual(self.notifications(), [])

        # 120 days later the contract enters the warning window
        self.assertEqual(process_contract_expiry(today=days(120)), {"expiring": 1, "expired": 0})
        self.assertEqual(self.notifications(), [CONTRACT_EXPIRING])
        self.assertEqual(process_contract_expiry(today=days(121)), {"expiring": 0, "expired": 0})

        self.assertEqual(process_contract_expiry(today=days(201)), {"expiring": 0, "expired": 1})
        self.assertEqual(self.notifications(), [CONTRACT_EXPIRED])

    def test_job_update_is_not_change_logged(self):
        from core.models import ObjectChange

        before = ObjectChange.objects.filter(
            changed_object_type=ObjectType.objects.get_for_model(SupportContract)
        ).count()
        process_contract_expiry(today=days(150))
        after = ObjectChange.objects.filter(
            changed_object_type=ObjectType.objects.get_for_model(SupportContract)
        ).count()
        self.assertEqual(before, after)
