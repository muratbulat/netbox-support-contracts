from django.urls import include, path
from utilities.urls import get_model_urls

from . import views  # noqa: F401 -- registers the model views consumed by get_model_urls()

app_name = "netbox_support_contracts"

urlpatterns = [
    # Support contracts (detail routes include coverage/, add-devices/ and remove-coverage/)
    path("contracts/", include(get_model_urls(app_name, "supportcontract", detail=False))),
    path("contracts/<int:pk>/", include(get_model_urls(app_name, "supportcontract"))),
    # Support coverage
    path("coverage/", include(get_model_urls(app_name, "supportcoverage", detail=False))),
    path("coverage/<int:pk>/", include(get_model_urls(app_name, "supportcoverage"))),
    # Hardware lifecycle (EoL)
    path("lifecycle/", include(get_model_urls(app_name, "hardwarelifecycleeol", detail=False))),
    path("lifecycle/<int:pk>/", include(get_model_urls(app_name, "hardwarelifecycleeol"))),
    # Device coverage report
    path("device-coverage/", views.DeviceCoverageListView.as_view(), name="device_coverage"),
]
