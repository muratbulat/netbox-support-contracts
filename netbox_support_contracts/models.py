from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel, PrimaryModel

from .choices import ContractStateChoices
from .services import contract_state, days_until

__all__ = ("HardwareLifecycleEoL", "SupportContract", "SupportCoverage")

currency_validator = RegexValidator(
    r"^[A-Za-z]{3}$", _("Enter a 3-letter ISO 4217 currency code (e.g. USD, EUR, TRY).")
)


class StateMixin:
    """Never-stale state/days remaining derived from an end date (None = open-ended)."""

    def _state_end_date(self):
        raise NotImplementedError

    @property
    def state(self):
        return contract_state(self._state_end_date())

    def get_state_color(self):
        return ContractStateChoices.colors.get(self.state)

    def get_state_display(self):
        return dict(ContractStateChoices)[self.state]

    @property
    def days_remaining(self):
        return days_until(self._state_end_date())


class SupportContract(StateMixin, PrimaryModel):
    manufacturer = models.ForeignKey(
        to="dcim.Manufacturer",
        on_delete=models.PROTECT,
        related_name="support_contracts",
        verbose_name=_("manufacturer"),
    )
    contract_number = models.CharField(verbose_name=_("contract number"), max_length=128)
    service_level = models.CharField(
        verbose_name=_("service level"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("e.g., 8x5xNBD, 24x7x4, Onsite NBD"),
    )
    start_date = models.DateField(verbose_name=_("start date"), null=True, blank=True)
    end_date = models.DateField(
        verbose_name=_("end date"),
        null=True,
        blank=True,
        help_text=_("Last covered day. Leave blank for open-ended."),
    )
    reseller = models.CharField(
        verbose_name=_("reseller"),
        max_length=128,
        blank=True,
        default="",
        help_text=_("Reseller, partner, or contractor"),
    )
    cost = models.DecimalField(verbose_name=_("cost"), max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(
        verbose_name=_("currency"),
        max_length=3,
        blank=True,
        default="USD",
        validators=[currency_validator],
    )
    portal_url = models.URLField(max_length=500, blank=True, default="", verbose_name=_("Vendor Portal URL"))
    renewal_notes = models.TextField(verbose_name=_("renewal notes"), blank=True, default="")
    tenant = models.ForeignKey(
        to="tenancy.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_contracts",
        verbose_name=_("tenant"),
    )
    # Last lifecycle state announced by the daily expiry job; maintained by the job only.
    notified_state = models.CharField(max_length=16, blank=True, default="", editable=False)

    clone_fields = (
        "manufacturer", "service_level", "start_date", "end_date", "reseller", "currency", "portal_url", "tenant",
    )

    class Meta:
        ordering = ("contract_number", "pk")
        verbose_name = _("support contract")
        verbose_name_plural = _("support contracts")
        constraints = (
            models.UniqueConstraint(
                "manufacturer",
                Lower("contract_number"),
                name="netbox_support_contracts_unique_mfr_contract_number",
                violation_error_message=_("This manufacturer already has a contract with this number."),
            ),
        )
        indexes = (models.Index(fields=["end_date"]),)
        permissions = (("view_cost", "Can view and edit contract commercial cost"),)

    def __str__(self):
        return self.contract_number

    def _state_end_date(self):
        return self.end_date

    def clean(self):
        super().clean()
        self.currency = (self.currency or "").strip().upper()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": _("End date cannot be earlier than start date.")})
        if self.cost is not None and not self.currency:
            raise ValidationError(
                {"currency": _("Specify a 3-letter currency code (e.g. USD, EUR, TRY) when cost is set.")}
            )

    def save(self, *args, **kwargs):
        self.currency = (self.currency or "").strip().upper()
        super().save(*args, **kwargs)

    @property
    def cost_display(self):
        """Formatted cost (e.g. "1,200.00 USD"). Callers must gate on services.may_view_cost()."""
        if self.cost is None:
            return ""
        return f"{self.cost:,.2f} {self.currency}".strip()


class SupportCoverage(StateMixin, NetBoxModel):
    contract = models.ForeignKey(
        to=SupportContract,
        on_delete=models.CASCADE,
        related_name="coverage",
        verbose_name=_("contract"),
    )
    device = models.ForeignKey(
        to="dcim.Device",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="support_coverage",
        verbose_name=_("device"),
    )
    virtual_machine = models.ForeignKey(
        to="virtualization.VirtualMachine",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="support_coverage",
        verbose_name=_("virtual machine"),
    )
    module = models.ForeignKey(
        to="dcim.Module",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="support_coverage",
        verbose_name=_("module"),
    )
    end_date = models.DateField(
        verbose_name=_("end date"),
        null=True,
        blank=True,
        help_text=_("Optional override end date for this specific item (defaults to contract end date)."),
    )
    sku = models.CharField(max_length=100, blank=True, default="", verbose_name=_("SKU / License Part Number"))
    notes = models.CharField(verbose_name=_("notes"), max_length=255, blank=True, default="")

    OBJECT_FIELDS = ("device", "virtual_machine", "module")

    class Meta:
        ordering = ("contract", "pk")
        verbose_name = _("support coverage")
        verbose_name_plural = _("support coverage")
        constraints = (
            models.UniqueConstraint(
                fields=("contract", "device"),
                condition=Q(device__isnull=False),
                name="netbox_support_contracts_unique_contract_device",
                violation_error_message=_("This device is already covered by this contract."),
            ),
            models.UniqueConstraint(
                fields=("contract", "virtual_machine"),
                condition=Q(virtual_machine__isnull=False),
                name="netbox_support_contracts_unique_contract_vm",
                violation_error_message=_("This virtual machine is already covered by this contract."),
            ),
            models.UniqueConstraint(
                fields=("contract", "module"),
                condition=Q(module__isnull=False),
                name="netbox_support_contracts_unique_contract_module",
                violation_error_message=_("This module is already covered by this contract."),
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(device__isnull=False, virtual_machine__isnull=False)
                    & ~Q(device__isnull=False, module__isnull=False)
                    & ~Q(virtual_machine__isnull=False, module__isnull=False)
                ),
                name="netbox_support_contracts_coverage_single_object",
                violation_error_message=_("Coverage may reference only one device, virtual machine, or module."),
            ),
            models.CheckConstraint(
                condition=(
                    Q(device__isnull=False) | Q(virtual_machine__isnull=False) | Q(module__isnull=False) | ~Q(sku="")
                ),
                name="netbox_support_contracts_coverage_object_or_sku",
                violation_error_message=_("Coverage requires a device, virtual machine, module, or SKU."),
            ),
        )

    def __str__(self):
        target = self.covered_object or self.sku or _("contract-level entry")
        return f"{self.contract} → {target}"

    @property
    def covered_object(self):
        return self.device or self.virtual_machine or self.module

    @property
    def effective_end_date(self):
        return self.end_date or self.contract.end_date

    def _state_end_date(self):
        return self.effective_end_date

    def clean(self):
        super().clean()
        selected = [name for name in self.OBJECT_FIELDS if getattr(self, f"{name}_id") is not None]
        if len(selected) > 1:
            message = _("Select only one of device, virtual machine, or module.")
            raise ValidationError(dict.fromkeys(selected, message))
        if not selected and not self.sku:
            raise ValidationError({
                "device": _(
                    "Select a device, virtual machine, or module (or enter a SKU for a contract-level license)."
                )
            })
        if selected and self.contract_id is not None:
            name = selected[0]
            duplicates = SupportCoverage.objects.filter(
                contract_id=self.contract_id, **{f"{name}_id": getattr(self, f"{name}_id")}
            ).exclude(pk=self.pk)
            if duplicates.exists():
                raise ValidationError({
                    name: _("{object} is already covered by contract {contract}.").format(
                        object=getattr(self, name), contract=self.contract
                    )
                })


class HardwareLifecycleEoL(StateMixin, PrimaryModel):
    device_type = models.OneToOneField(
        to="dcim.DeviceType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="support_eol",
        verbose_name=_("device type"),
    )
    module_type = models.OneToOneField(
        to="dcim.ModuleType",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="support_eol",
        verbose_name=_("module type"),
    )
    end_of_sale = models.DateField(verbose_name=_("end of sale"), null=True, blank=True)
    end_of_maintenance = models.DateField(verbose_name=_("end of maintenance"), null=True, blank=True)
    end_of_support = models.DateField(verbose_name=_("end of support"), null=True, blank=True)
    notice_url = models.URLField(verbose_name=_("notice URL"), max_length=500, blank=True, default="")

    class Meta:
        ordering = ("end_of_support", "pk")
        verbose_name = _("hardware lifecycle (EoL)")
        verbose_name_plural = _("hardware lifecycle (EoL)")
        constraints = (
            models.CheckConstraint(
                condition=(
                    Q(device_type__isnull=False, module_type__isnull=True)
                    | Q(device_type__isnull=True, module_type__isnull=False)
                ),
                name="netbox_support_contracts_eol_single_type",
                violation_error_message=_("Select exactly one of device type or module type."),
            ),
        )

    def __str__(self):
        return str(self.hardware_type or _("Hardware lifecycle"))

    @property
    def hardware_type(self):
        return self.device_type or self.module_type

    @property
    def manufacturer(self):
        return self.hardware_type.manufacturer if self.hardware_type else None

    def _state_end_date(self):
        return self.end_of_support

    def clean(self):
        super().clean()
        if (self.device_type_id is None) == (self.module_type_id is None):
            message = _("Select exactly one of device type or module type.")
            raise ValidationError({"device_type": message, "module_type": message})
        milestones = (
            ("end_of_sale", self.end_of_sale),
            ("end_of_maintenance", self.end_of_maintenance),
            ("end_of_support", self.end_of_support),
        )
        known = [(name, value) for name, value in milestones if value is not None]
        for (earlier_name, earlier), (later_name, later) in zip(known, known[1:], strict=False):
            if later < earlier:
                raise ValidationError({
                    later_name: _("{later} cannot be earlier than {earlier}.").format(
                        later=self._meta.get_field(later_name).verbose_name.capitalize(),
                        earlier=self._meta.get_field(earlier_name).verbose_name,
                    )
                })
