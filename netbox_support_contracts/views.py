from dcim.models import Device, DeviceRole, DeviceType, Module, Site
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import View
from netbox.object_actions import AddObject, BulkDelete, BulkEdit, BulkExport, BulkImport
from netbox.views import generic
from utilities.exceptions import PermissionsViolation
from utilities.views import ContentTypePermissionRequiredMixin, ViewTab, register_model_view
from virtualization.models import VirtualMachine

from . import filtersets, forms, tables
from .models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from .services import (
    MAX_BULK_ITEMS,
    annotate_contracts,
    annotate_coverage,
    annotate_devices,
    bulk_add_coverage,
    get_warning_days,
    may_view_cost,
)

PLUGIN = "netbox_support_contracts"
LIST_ACTIONS = (AddObject, BulkImport, BulkExport, BulkEdit, BulkDelete)


def coverage_queryset():
    return annotate_coverage(
        SupportCoverage.objects.select_related(
            "contract__manufacturer", "device__device_type", "device__site", "virtual_machine", "module__module_type",
        )
    )


def contract_queryset():
    return annotate_contracts(SupportContract.objects.select_related("manufacturer", "tenant"))


#
# Support contracts
#

@register_model_view(SupportContract, "list", path="", detail=False)
class SupportContractListView(generic.ObjectListView):
    queryset = contract_queryset()
    table = tables.SupportContractTable
    filterset = filtersets.SupportContractFilterSet
    filterset_form = forms.SupportContractFilterForm
    actions = LIST_ACTIONS


@register_model_view(SupportContract)
class SupportContractView(generic.ObjectView):
    queryset = contract_queryset()
    template_name = f"{PLUGIN}/supportcontract.html"

    def get_extra_context(self, request, instance):
        return {
            "can_view_cost": may_view_cost(request.user),
            "state_badge": tables.state_badge(instance.state),
            "days_badge": tables.days_badge(instance.days_remaining),
            "warning_days": get_warning_days(),
        }


@register_model_view(SupportContract, "coverage", path="coverage")
class SupportContractCoveredObjectsView(generic.ObjectChildrenView):
    queryset = SupportContract.objects.all()
    child_model = SupportCoverage
    table = tables.SupportCoverageTable
    filterset = filtersets.SupportCoverageFilterSet
    filterset_form = forms.SupportCoverageFilterForm
    template_name = f"{PLUGIN}/supportcontract_coverage.html"
    actions = (BulkEdit, BulkDelete)
    tab = ViewTab(
        label=gettext_lazy("Covered Devices & Objects"),
        badge=lambda obj: obj.coverage.count(),
        permission=f"{PLUGIN}.view_supportcoverage",
        weight=500,
    )

    def get_children(self, request, parent):
        return coverage_queryset().restrict(request.user, "view").filter(contract=parent)


@register_model_view(SupportContract, "add", detail=False)
@register_model_view(SupportContract, "edit")
class SupportContractEditView(generic.ObjectEditView):
    queryset = SupportContract.objects.all()
    form = forms.SupportContractForm


@register_model_view(SupportContract, "delete")
class SupportContractDeleteView(generic.ObjectDeleteView):
    queryset = SupportContract.objects.all()


@register_model_view(SupportContract, "bulk_import", path="import", detail=False)
class SupportContractBulkImportView(generic.BulkImportView):
    queryset = SupportContract.objects.all()
    model_form = forms.SupportContractImportForm


@register_model_view(SupportContract, "bulk_edit", path="edit", detail=False)
class SupportContractBulkEditView(generic.BulkEditView):
    queryset = contract_queryset()
    filterset = filtersets.SupportContractFilterSet
    table = tables.SupportContractTable
    form = forms.SupportContractBulkEditForm


@register_model_view(SupportContract, "bulk_delete", path="delete", detail=False)
class SupportContractBulkDeleteView(generic.BulkDeleteView):
    queryset = contract_queryset()
    filterset = filtersets.SupportContractFilterSet
    table = tables.SupportContractTable


#
# Bulk coverage workflow
#

class CoveragePermissionView(ContentTypePermissionRequiredMixin, View):
    permission_required = ""

    def get_required_permission(self):
        return self.permission_required


@register_model_view(SupportContract, "add_devices", path="add-devices")
class BulkAddCoverageView(CoveragePermissionView):
    """Cover existing devices/VMs/modules with a contract by picking them, filtering, or pasting serial numbers."""

    permission_required = f"{PLUGIN}.add_supportcoverage"
    template_name = f"{PLUGIN}/bulk_add_coverage.html"

    def get_form(self, request, data=None, initial=None):
        form = forms.BulkCoverageAddForm(data, initial=initial)
        # Only objects the user may view can be picked (and covered).
        restricted = {
            "devices": Device, "virtual_machines": VirtualMachine, "modules": Module,
            "site": Site, "device_type": DeviceType, "role": DeviceRole,
        }
        for name, model in restricted.items():
            form.fields[name].queryset = model.objects.restrict(request.user, "view")
        return form

    def render_page(self, request, contract, form, result=None):
        return render(request, self.template_name, {
            "object": contract,
            "form": form,
            "result": result,
            "max_items": MAX_BULK_ITEMS,
            "return_url": reverse(f"plugins:{PLUGIN}:supportcontract_coverage", args=[contract.pk]),
        })

    def get(self, request, pk):
        contract = get_object_or_404(SupportContract.objects.restrict(request.user, "view"), pk=pk)
        return self.render_page(request, contract, self.get_form(request))

    def post(self, request, pk):
        contract = get_object_or_404(SupportContract.objects.restrict(request.user, "view"), pk=pk)
        form = self.get_form(request, request.POST)
        if not form.is_valid():
            return self.render_page(request, contract, form)

        devices = list(form.cleaned_data["devices"])
        if form.has_device_filters:
            matching = form.filtered_devices(Device.objects.restrict(request.user, "view"))
            if matching.count() > MAX_BULK_ITEMS:
                form.add_error(None, _(
                    "The selected filters match more than {max} devices. Narrow them down and try again."
                ).format(max=MAX_BULK_ITEMS))
                return self.render_page(request, contract, form)
            devices.extend(matching)

        try:
            result = bulk_add_coverage(
                contract,
                user=request.user,
                devices=devices,
                virtual_machines=form.cleaned_data["virtual_machines"],
                modules=form.cleaned_data["modules"],
                serials=form.cleaned_data["serials"],
                sku=form.cleaned_data["sku"],
                quantity=form.cleaned_data["quantity"],
                end_date=form.cleaned_data["end_date"],
                notes=form.cleaned_data["notes"],
            )
        except PermissionsViolation:
            form.add_error(None, _("You do not have permission to cover one or more of the selected objects."))
            return self.render_page(request, contract, form)
        except ValidationError as e:
            form.add_error(None, e)
            return self.render_page(request, contract, form)

        summary = _("{created} added, {existing} already covered.").format(
            created=len(result.created), existing=len(result.existing)
        )
        if not result.needs_review:
            messages.success(request, summary)
            return redirect(reverse(f"plugins:{PLUGIN}:supportcontract_coverage", args=[contract.pk]))

        # Keep the user on the page with only the serials that still need attention.
        messages.warning(request, _("{summary} Some serial numbers need review.").format(summary=summary))
        retry = self.get_form(request, initial={
            "serials": "\n".join([*result.unmatched, *result.ambiguous]),
            "sku": form.cleaned_data["sku"],
            "quantity": form.cleaned_data["quantity"],
            "end_date": form.cleaned_data["end_date"],
            "notes": form.cleaned_data["notes"],
        })
        return self.render_page(request, contract, retry, result)


#
# Support coverage
#

@register_model_view(SupportCoverage, "list", path="", detail=False)
class SupportCoverageListView(generic.ObjectListView):
    queryset = coverage_queryset()
    table = tables.SupportCoverageTable
    filterset = filtersets.SupportCoverageFilterSet
    filterset_form = forms.SupportCoverageFilterForm
    actions = LIST_ACTIONS


@register_model_view(SupportCoverage)
class SupportCoverageView(generic.ObjectView):
    queryset = coverage_queryset()
    template_name = f"{PLUGIN}/supportcoverage.html"

    def get_extra_context(self, request, instance):
        return {
            "state_badge": tables.state_badge(instance.state),
            "days_badge": tables.days_badge(instance.days_remaining),
        }


@register_model_view(SupportCoverage, "add", detail=False)
@register_model_view(SupportCoverage, "edit")
class SupportCoverageEditView(generic.ObjectEditView):
    queryset = SupportCoverage.objects.all()
    form = forms.SupportCoverageForm


@register_model_view(SupportCoverage, "delete")
class SupportCoverageDeleteView(generic.ObjectDeleteView):
    queryset = SupportCoverage.objects.all()


@register_model_view(SupportCoverage, "bulk_import", path="import", detail=False)
class SupportCoverageBulkImportView(generic.BulkImportView):
    queryset = SupportCoverage.objects.all()
    model_form = forms.SupportCoverageImportForm


@register_model_view(SupportCoverage, "bulk_edit", path="edit", detail=False)
class SupportCoverageBulkEditView(generic.BulkEditView):
    queryset = coverage_queryset()
    filterset = filtersets.SupportCoverageFilterSet
    table = tables.SupportCoverageTable
    form = forms.SupportCoverageBulkEditForm


@register_model_view(SupportCoverage, "bulk_delete", path="delete", detail=False)
class SupportCoverageBulkDeleteView(generic.BulkDeleteView):
    queryset = coverage_queryset()
    filterset = filtersets.SupportCoverageFilterSet
    table = tables.SupportCoverageTable


#
# Hardware lifecycle (EoL)
#

def eol_queryset():
    return HardwareLifecycleEoL.objects.select_related("device_type__manufacturer", "module_type__manufacturer")


@register_model_view(HardwareLifecycleEoL, "list", path="", detail=False)
class HardwareLifecycleEoLListView(generic.ObjectListView):
    queryset = eol_queryset()
    table = tables.HardwareLifecycleEoLTable
    filterset = filtersets.HardwareLifecycleEoLFilterSet
    filterset_form = forms.HardwareLifecycleEoLFilterForm
    actions = LIST_ACTIONS


@register_model_view(HardwareLifecycleEoL)
class HardwareLifecycleEoLView(generic.ObjectView):
    queryset = eol_queryset()
    template_name = f"{PLUGIN}/hardwarelifecycleeol.html"

    def get_extra_context(self, request, instance):
        hardware_type = instance.hardware_type
        field = "device__device_type" if instance.device_type_id else "module__module_type"
        covered = SupportCoverage.objects.restrict(request.user, "view").filter(**{field: hardware_type})
        return {
            "state_badge": tables.state_badge(instance.state) if instance.end_of_support else None,
            "days_badge": tables.days_badge(instance.days_remaining) if instance.end_of_support else None,
            "covered_count": covered.count(),
            "covered_past_eos_count": covered_past_eos(covered, instance.end_of_support),
        }


def covered_past_eos(coverage, end_of_support):
    """Number of coverage rows (open-ended included) running past the given End of Support date."""
    if end_of_support is None:
        return 0
    return annotate_coverage(coverage).filter(
        Q(effective_end__isnull=True) | Q(effective_end__gt=end_of_support)
    ).count()


@register_model_view(HardwareLifecycleEoL, "add", detail=False)
@register_model_view(HardwareLifecycleEoL, "edit")
class HardwareLifecycleEoLEditView(generic.ObjectEditView):
    queryset = HardwareLifecycleEoL.objects.all()
    form = forms.HardwareLifecycleEoLForm


@register_model_view(HardwareLifecycleEoL, "delete")
class HardwareLifecycleEoLDeleteView(generic.ObjectDeleteView):
    queryset = HardwareLifecycleEoL.objects.all()


@register_model_view(HardwareLifecycleEoL, "bulk_import", path="import", detail=False)
class HardwareLifecycleEoLBulkImportView(generic.BulkImportView):
    queryset = HardwareLifecycleEoL.objects.all()
    model_form = forms.HardwareLifecycleEoLImportForm


@register_model_view(HardwareLifecycleEoL, "bulk_edit", path="edit", detail=False)
class HardwareLifecycleEoLBulkEditView(generic.BulkEditView):
    queryset = eol_queryset()
    filterset = filtersets.HardwareLifecycleEoLFilterSet
    table = tables.HardwareLifecycleEoLTable
    form = forms.HardwareLifecycleEoLBulkEditForm


@register_model_view(HardwareLifecycleEoL, "bulk_delete", path="delete", detail=False)
class HardwareLifecycleEoLBulkDeleteView(generic.BulkDeleteView):
    queryset = eol_queryset()
    filterset = filtersets.HardwareLifecycleEoLFilterSet
    table = tables.HardwareLifecycleEoLTable


#
# Device coverage report
#

class DeviceCoverageListView(generic.ObjectListView):
    """All devices the user may view with their overall coverage status (incl. "Not covered")."""

    queryset = annotate_devices(Device.objects.select_related("device_type__manufacturer", "site", "role"))
    table = tables.DeviceCoverageTable
    filterset = filtersets.DeviceCoverageFilterSet
    filterset_form = forms.DeviceCoverageFilterForm
    template_name = f"{PLUGIN}/device_coverage.html"
    actions = ()
