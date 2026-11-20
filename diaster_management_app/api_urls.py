from django.urls import path

from . import views

app_name = "disaster_management_api"

urlpatterns = [
    path("incidents/report/", views.report_incident, name="report_incident"),
    path("responders/availability/", views.set_responder_availability, name="set_responder_availability"),
]
