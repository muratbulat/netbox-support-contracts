from netbox.api.routers import NetBoxRouter

from . import views

app_name = "netbox_support_contracts-api"

router = NetBoxRouter()
router.register("contracts", views.SupportContractViewSet)
router.register("coverage", views.SupportCoverageViewSet)
router.register("lifecycle", views.HardwareLifecycleEoLViewSet)

urlpatterns = router.urls
