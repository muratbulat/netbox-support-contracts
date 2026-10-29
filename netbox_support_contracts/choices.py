from django.utils.translation import gettext_lazy as _
from utilities.choices import ChoiceSet

__all__ = ("ContractStateChoices", "ContractTypeChoices", "CoverageStatusChoices", "CurrencyChoices")


class ContractStateChoices(ChoiceSet):
    """Lifecycle state of a contract (or a coverage row), computed from its end date. Never stored."""

    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"

    CHOICES = (
        (ACTIVE, _("Active"), "green"),
        (EXPIRING, _("Expiring"), "yellow"),
        (EXPIRED, _("Expired"), "red"),
    )


class ContractTypeChoices(ChoiceSet):
    """What kind of agreement a contract is: OEM warranty, vendor support, third-party maintenance, or software."""

    WARRANTY = "warranty"
    VENDOR_SUPPORT = "vendor_support"
    TPM = "tpm"
    SOFTWARE = "software"

    CHOICES = (
        (WARRANTY, _("OEM Warranty"), "blue"),
        (VENDOR_SUPPORT, _("Vendor Support / Care Pack"), "cyan"),
        (TPM, _("Third-Party Maintenance (TPM)"), "purple"),
        (SOFTWARE, _("Software License / Subscription"), "indigo"),
    )


class CoverageStatusChoices(ChoiceSet):
    """Overall support coverage of a device or virtual machine across all of its contracts."""

    COVERED = "covered"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    NONE = "none"

    CHOICES = (
        (COVERED, _("Covered"), "green"),
        (EXPIRING, _("Expiring"), "yellow"),
        (EXPIRED, _("Expired"), "red"),
        (NONE, _("Not covered"), "gray"),
    )


class CurrencyChoices(ChoiceSet):
    """Common ISO 4217 codes offered as suggestions; any 3-letter code is accepted."""

    CHOICES = (
        ("USD", "USD"),
        ("EUR", "EUR"),
        ("GBP", "GBP"),
        ("TRY", "TRY"),
        ("JPY", "JPY"),
        ("CHF", "CHF"),
        ("CAD", "CAD"),
        ("AUD", "AUD"),
        ("CNY", "CNY"),
        ("INR", "INR"),
    )
