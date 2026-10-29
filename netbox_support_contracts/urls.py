from django.urls import include, path
from utilities.urls import get_model_urls

from . import views  # noqa: F401 -- registers the model views consumed by get_model_urls()

app_name = "netbox_support_contracts"

urlpatterns = [
    # Support contracts (detail routes include coverage/ and add-devices/)
    path("contracts/", include(get_model_urls(app_name, "supportcontract", detail=False))),
    path("contracts/<int:pk>/", include(get_model_urls(app_name, "supportcontract"))),
    # Support coverage
    path("coverage/", include(get_model_urls(app_name, "supportcoverage", detail=False))),
    path("coverage/<int:pk>/", include(get_model_urls(app_name, "supportcoverage"))),
    # Hardware lifecycle (EoL)
    path("lifecycle/", include(get_model_urls(app_name, "hardwarelifecycleeol", detail=False))),
    path("lifecycle/<int:pk>/", include(get_model_urls(app_name, "hardwarelifecycleeol"))),
    # Partner companies
    path("integrators/", include(get_model_urls(app_name, "integrator", detail=False))),
    path("integrators/<int:pk>/", include(get_model_urls(app_name, "integrator"))),
    path("maintenance-companies/", include(get_model_urls(app_name, "maintenancecompany", detail=False))),
    path("maintenance-companies/<int:pk>/", include(get_model_urls(app_name, "maintenancecompany"))),
    # Device coverage report
    path("device-coverage/", views.DeviceCoverageListView.as_view(), name="device_coverage"),
]
