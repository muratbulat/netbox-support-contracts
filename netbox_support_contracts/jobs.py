"""
Daily contract expiry job.

Contract state is computed on the fly, so nothing "changes" on the day a contract starts expiring. This job turns
those silent transitions into NetBox events: once a day it compares each contract's current state with the last
state it announced (SupportContract.notified_state) and, on a transition into "expiring" or "expired":

* notifies every user subscribed to the contract (bell icon), and
* runs the enabled Event Rules bound to SupportContract for the matching event type (webhooks, scripts,
  notification groups).

A renewed contract quietly returns to "active" and is announced again on its next transition.
"""

from core.choices import JobIntervalChoices
from core.models import ObjectType
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from extras.events import EventContext, process_event_rules
from extras.models import EventRule, Notification, Subscription
from netbox.events import EVENT_TYPE_KIND_DANGER, EVENT_TYPE_KIND_WARNING, EventType, get_event_type
from netbox.jobs import JobRunner, system_job

from .choices import ContractStateChoices
from .models import SupportContract
from .services import contract_state, get_warning_days

__all__ = ("CONTRACT_EXPIRED", "CONTRACT_EXPIRING", "ContractExpiryJob", "process_contract_expiry")

CONTRACT_EXPIRING = "netbox_support_contracts.contract_expiring"
CONTRACT_EXPIRED = "netbox_support_contracts.contract_expired"

STATE_EVENTS = {
    ContractStateChoices.EXPIRING: CONTRACT_EXPIRING,
    ContractStateChoices.EXPIRED: CONTRACT_EXPIRED,
}

for _name, _text, _kind in (
    (CONTRACT_EXPIRING, _("Support contract expiring"), EVENT_TYPE_KIND_WARNING),
    (CONTRACT_EXPIRED, _("Support contract expired"), EVENT_TYPE_KIND_DANGER),
):
    if get_event_type(_name) is None:
        EventType(_name, _text, kind=_kind).register()


def emit_contract_event(contract, event_type):
    """Notify the contract's subscribers and run matching event rules. Returns the number of users notified."""
    object_type = ObjectType.objects.get_for_model(SupportContract)
    users = list(
        Subscription.objects.filter(object_type=object_type, object_id=contract.pk).values_list("user", flat=True)
    )
    Notification.objects.filter(object_type=object_type, object_id=contract.pk, user__in=users).delete()
    Notification.objects.bulk_create([
        Notification(
            user_id=user,
            object=contract,
            object_repr=Notification.get_object_repr(contract),
            event_type=event_type,
        )
        for user in users
    ])
    event_rules = EventRule.objects.filter(
        event_types__contains=[event_type], enabled=True, object_types=object_type
    )
    process_event_rules(event_rules, object_type, EventContext(
        object_type=object_type,
        object_id=contract.pk,
        object=contract,
        event_type=event_type,
        user=None,
    ))
    return len(users)


def process_contract_expiry(today=None, logger=None):
    """Announce every contract whose state changed since the last run. Returns {"expiring": n, "expired": n}."""
    today = today or timezone.localdate()
    warning_days = get_warning_days()
    announced = {ContractStateChoices.EXPIRING: 0, ContractStateChoices.EXPIRED: 0}
    contracts = SupportContract.objects.select_related("manufacturer", "maintenance_company").order_by("pk")
    for contract in contracts.iterator(chunk_size=500):
        state = contract_state(contract.end_date, warning_days, today)
        if state == contract.notified_state:
            continue
        # Queryset update: no change-log entry, no "object updated" notification for a bookkeeping field.
        SupportContract.objects.filter(pk=contract.pk).update(notified_state=state)
        if event_type := STATE_EVENTS.get(state):
            notified = emit_contract_event(contract, event_type)
            announced[state] += 1
            if logger:
                logger.info(f"{contract} ({contract.provider}) is {state}; notified {notified} subscriber(s)")
    return announced


@system_job(interval=JobIntervalChoices.INTERVAL_DAILY)
class ContractExpiryJob(JobRunner):
    class Meta:
        name = "Support Contract Expiry Check"

    def run(self, *args, **kwargs):
        announced = process_contract_expiry(logger=self.logger)
        self.logger.info(
            f"Announced {announced['expiring']} expiring and {announced['expired']} expired support contract(s)"
        )
