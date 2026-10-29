from django.conf import settings
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import translation
from utilities.testing import TestCase

from netbox_support_contracts import navigation
from netbox_support_contracts.forms import CURRENCY_HELP, BulkCoverageAddForm, SupportCoverageForm
from netbox_support_contracts.models import HardwareLifecycleEoL, SupportContract
from netbox_support_contracts.tables import SupportContractTable, coverage_badge, state_badge
from netbox_support_contracts.views import SupportContractCoveredObjectsView
from netbox_support_contracts.widgets import SupportCoverageSummaryWidget

from . import cover, days, make_contract, make_device, make_device_type

PLUGIN = "netbox_support_contracts"


def url(name, *args):
    return reverse(f"plugins:{PLUGIN}:{name}", args=args)


class LazyStringTests(TestCase):
    """Strings defined at import time must follow the language active when they are rendered."""

    def check(self, value, english, turkish):
        # Turkish first, then English, then Turkish again: nothing may be stuck in one language.
        for language, expected in (("tr", turkish), ("en", english), ("tr", turkish)):
            with self.subTest(language=language), translation.override(language):
                self.assertIn(expected, str(value))

    def test_navigation(self):
        items = {str(item.link): item for group in navigation.menu.groups for item in group.items}
        item = items[f"plugins:{PLUGIN}:supportcontract_list"]
        self.check(item.link_text, "Support Contracts", "Destek Sözleşmeleri")
        self.check(navigation.menu.label, "Support Contracts", "Destek Sözleşmeleri")
        self.check(navigation.menu.groups[1].label, "Lifecycle", "Yaşam Döngüsü")
        self.check(items[f"plugins:{PLUGIN}:device_coverage"].link_text, "Device Coverage", "Cihaz Kapsam Raporu")

    def test_view_tab_label(self):
        label = SupportContractCoveredObjectsView.tab.label
        self.check(label, "Covered Devices & Objects", "Kapsanan Cihazlar ve Nesneler")

    def test_form_labels_and_format_lazy_help_texts(self):
        self.check(CURRENCY_HELP, "3-letter ISO 4217 code, e.g. USD", "3 harfli ISO 4217 kodu, örn. USD")
        form = BulkCoverageAddForm()
        self.check(form.fields["serials"].help_text, "Paste up to 5000 serial numbers", "en fazla 5000 seri numarası")
        self.check(form.fields["serials"].label, "Serial numbers", "Seri numaraları")
        self.check(form.fieldsets[2].name, "Option B: Paste serial numbers", "Seçenek B: Seri numaralarını yapıştırın")
        self.check(SupportCoverageForm().fields["contract"].label, "Contract", "Sözleşme")

    def test_choice_badges(self):
        for language, expected in (("tr", "Süresi Dolmuş"), ("en", "Expired")):
            with translation.override(language):
                self.assertIn(expected, state_badge("expired"))
        for language, expected in (("tr", "Kapsam Dışı"), ("en", "Not covered")):
            with translation.override(language):
                self.assertIn(expected, coverage_badge("none"))

    def test_table_headers(self):
        table = SupportContractTable(SupportContract.objects.none())
        self.check(table.columns["state"].header, "State", "Durum")
        self.check(table.columns["days_remaining"].header, "Days Remaining", "Kalan Gün")
        self.check(table.columns["device_count"].header, "Devices", "Cihazlar")

    def test_remaining_days_display(self):
        contract = SupportContract(end_date=days(45))
        for language, expected in (("tr", "45 gün kaldı"), ("en", "45 days left")):
            with translation.override(language):
                self.assertEqual(contract.remaining_days_display, expected)
        contract.end_date = days(-3)
        for language, expected in (("tr", "Süresi 3 gün önce doldu"), ("en", "Expired 3 days ago")):
            with translation.override(language):
                self.assertEqual(contract.remaining_days_display, expected)
        contract.end_date = None
        for language, expected in (("tr", "Süresiz"), ("en", "Open-ended")):
            with translation.override(language):
                self.assertEqual(contract.remaining_days_display, expected)

    def test_model_metadata(self):
        self.check(SupportContract._meta.verbose_name_plural, "support contracts", "destek sözleşmeleri")
        from django.apps import apps

        self.check(apps.get_app_config(PLUGIN).verbose_name, "NetBox Support Contracts", "NetBox Destek Sözleşmeleri")


@override_settings(LOGIN_REQUIRED=True, EXEMPT_VIEW_PERMISSIONS=[])
class TemplateTranslationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.device_type = make_device_type("Switch-48")
        cls.device = make_device("Device-01", serial="SN-10001", device_type=cls.device_type)
        cls.contract = make_contract("CONT-2026-001", end_date=days(45), service_level="24x7x4")
        cover(cls.contract, cls.device)
        HardwareLifecycleEoL.objects.create(device_type=cls.device_type, end_of_support=days(-1))

    def setUp(self):
        super().setUp()
        self.add_permissions(
            f"{PLUGIN}.view_supportcontract", f"{PLUGIN}.view_supportcoverage", f"{PLUGIN}.add_supportcoverage",
            f"{PLUGIN}.delete_supportcoverage", f"{PLUGIN}.view_hardwarelifecycleeol", "dcim.view_device",
            "dcim.view_devicetype",
        )

    def get(self, language, path, **params):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200)
        return response

    PAGES = {
        "contract": (
            lambda self: self.contract.get_absolute_url(),
            ["Sözleşme Özeti", "Ticari Bilgiler", "45 gün kaldı", "Süresi Yakında Dolacak", "Cihaz / Seri No Ekle"],
            ["Contract Overview", "Commercial", "45 days left", "Expiring", "Add Devices / Serials"],
        ),
        "coverage_tab": (
            lambda self: url("supportcontract_coverage", self.contract.pk),
            ["Kapsanan Cihazlar ve Nesneler", "Cihaz / Seri No Ekle", "Kalan Gün"],
            ["Covered Devices &amp; Objects", "Add Devices / Serials", "Days Remaining"],
        ),
        "device_coverage": (
            lambda self: url("device_coverage"),
            ["Cihaz Kapsam Raporu", "Kapsam Dahilinde"],
            ["Device Coverage", "Covered"],
        ),
        "bulk_add": (
            lambda self: url("supportcontract_add_devices", self.contract.pk),
            ["CONT-2026-001 sözleşmesine cihaz ekle", "Seçenek B: Seri numaralarını yapıştırın", "Kapsam Ekle"],
            ["Add Devices to CONT-2026-001", "Option B: Paste serial numbers", "Add Coverage"],
        ),
        "device_panel": (
            lambda self: self.device.get_absolute_url(),
            ["Destek ve Garanti Kapsamı", "Sözleşmeye Ata", "Ömrünü tamamlamış (EoL) donanım"],
            ["Support &amp; Warranty Coverage", "Assign Contract", "End-of-life hardware under active"],
        ),
        "lifecycle_panel": (
            lambda self: self.device_type.get_absolute_url(),
            ["Üretici Yaşam Döngüsü (EoL)", "Destek Sonu"],
            ["Vendor Lifecycle (EoL)", "End of Support"],
        ),
    }

    def test_pages_render_in_turkish_then_english(self):
        for name, (path, turkish, english) in self.PAGES.items():
            for language, expected, unexpected in (("tr", turkish, english[:1]), ("en", english, turkish[:1])):
                with self.subTest(page=name, language=language):
                    response = self.get(language, path(self))
                    for text in expected:
                        self.assertContains(response, text)
                    for text in unexpected:
                        self.assertNotContains(response, text)

    def test_bulk_add_review_counts_in_turkish(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "tr"
        page = url("supportcontract_add_devices", self.contract.pk)
        response = self.client.post(page, {"serials": "SN-10001 SN-404"})
        self.assertContains(response, "0 eklendi")
        self.assertContains(response, "1 zaten kapsamda")
        self.assertContains(response, "1 bulunamadı")

    def test_dashboard_widget(self):
        request = RequestFactory().get("/")
        request.user = self.user
        for language, expected in (("tr", "Kapsam Dışı"), ("en", "Not Covered")):
            with translation.override(language):
                self.assertIn(expected, SupportCoverageSummaryWidget().render(request))
