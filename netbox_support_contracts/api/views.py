from dcim.api.serializers import DeviceSerializer
from dcim.models import Device, Module
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from netbox.api.authentication import TokenPermissions
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from utilities.exceptions import PermissionsViolation
from virtualization.models import VirtualMachine

from .. import filtersets
from ..models import HardwareLifecycleEoL, SupportContract, SupportCoverage
from ..services import annotate_contracts, bulk_add_coverage, bulk_remove_coverage, coverage_summary
from . import serializers


class DeletePermissionOnPost(TokenPermissions):
    """POST requires the model's delete permission (for removal actions); token write checks still apply."""

    perms_map = {**TokenPermissions.perms_map, "POST": ["%(app_label)s.delete_%(model_name)s"]}


def get_contract(request, contract_id):
    try:
        return SupportContract.objects.restrict(request.user, "view").get(pk=contract_id)
    except SupportContract.DoesNotExist:
        raise ValidationError({"contract_id": [_("Unknown or inaccessible contract.")]}) from None


def resolve_ids(request, model, ids, field):
    """Objects for the given IDs that the user may view; any other ID is a validation error."""
    objects = list(model.objects.restrict(request.user, "view").filter(pk__in=ids))
    missing = sorted(set(ids) - {obj.pk for obj in objects})
    if missing:
        raise ValidationError({field: [_("Unknown or inaccessible IDs: {ids}").format(ids=missing)]})
    return objects


def object_ref(obj):
    return {"id": obj.pk, "object_type": obj._meta.label_lower, "display": str(obj)}


class SupportContractViewSet(NetBoxModelViewSet):
    queryset = annotate_contracts(SupportContract.objects.all())
    serializer_class = serializers.SupportContractSerializer
    filterset_class = filtersets.SupportContractFilterSet

    @extend_schema(responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Device coverage KPIs and the soonest-ending contracts (as on the dashboard widget)."""
        summary = coverage_summary(user=request.user)
        context = {"request": request}
        return Response({
            "warning_days": summary["warning_days"],
            "devices": summary["devices"],
            "soonest_contracts": [
                {
                    **serializers.SupportContractSerializer(contract, nested=True, context=context).data,
                    "manufacturer": str(contract.manufacturer),
                    "state": contract.state,
                    "days_remaining": contract.days_remaining,
                    "device_count": contract.device_count,
                }
                for contract in summary["soonest_contracts"]
            ],
        })


class SupportCoverageViewSet(NetBoxModelViewSet):
    queryset = SupportCoverage.objects.all()
    serializer_class = serializers.SupportCoverageSerializer
    filterset_class = filtersets.SupportCoverageFilterSet

    @extend_schema(request=serializers.BulkAddCoverageSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(detail=False, methods=["post"], url_path="bulk-add")
    def bulk_add(self, request):
        """Cover devices/VMs/modules (by ID and/or pasted serial numbers) with a contract."""
        params = serializers.BulkAddCoverageSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        contract = get_contract(request, data["contract_id"])
        try:
            result = bulk_add_coverage(
                contract,
                user=request.user,
                devices=resolve_ids(request, Device, data["device_ids"], "device_ids"),
                virtual_machines=resolve_ids(
                    request, VirtualMachine, data["virtual_machine_ids"], "virtual_machine_ids"
                ),
                modules=resolve_ids(request, Module, data["module_ids"], "module_ids"),
                serials=data["serials"],
                sku=data["sku"],
                quantity=data["quantity"],
                end_date=data["end_date"],
                notes=data["notes"],
            )
        except PermissionsViolation:
            raise PermissionDenied(_("Object-level permissions prevent covering one or more objects.")) from None
        except DjangoValidationError as e:
            raise ValidationError(e.messages) from None
        context = {"request": request}
        return Response(
            {
                "created": [
                    serializers.SupportCoverageSerializer(row, nested=True, context=context).data
                    for row in result.created
                ],
                "existing": [object_ref(obj) for obj in result.existing],
                "matched": [DeviceSerializer(device, nested=True, context=context).data for device in result.matched],
                "unmatched": result.unmatched,
                "ambiguous": result.ambiguous,
            },
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )

    @extend_schema(request=serializers.BulkRemoveCoverageSerializer, responses={200: OpenApiTypes.OBJECT})
    @action(
        detail=False,
        methods=["post"],
        url_path="bulk-remove",
        permission_classes=[DeletePermissionOnPost],
    )
    def bulk_remove(self, request):
        """Remove coverage rows from a contract, selected by coverage ID and/or covered object ID."""
        params = serializers.BulkRemoveCoverageSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        contract = get_contract(request, data["contract_id"])
        try:
            removed = bulk_remove_coverage(
                contract,
                user=request.user,
                coverage_ids=data["coverage_ids"],
                device_ids=data["device_ids"],
                virtual_machine_ids=data["virtual_machine_ids"],
                module_ids=data["module_ids"],
            )
        except PermissionsViolation:
            raise PermissionDenied(_("Object-level permissions prevent removing one or more rows.")) from None
        return Response({"removed": removed})


class HardwareLifecycleEoLViewSet(NetBoxModelViewSet):
    queryset = HardwareLifecycleEoL.objects.all()
    serializer_class = serializers.HardwareLifecycleEoLSerializer
    filterset_class = filtersets.HardwareLifecycleEoLFilterSet
