from django.urls import include, path

from . import views
from . import disaster_views as dv

app_name = "disaster_management"

urlpatterns = [
    path("", views.login_view, name="home"),
    path("health/", views.health_check, name="health_check"),
    path("login/", views.login_view, name="login"),
    path("register/", views.register_view, name="register"),
    path("register/reporter/", views.register_with_role_view, {"role": "reporter"}, name="register_reporter"),
    path("register/responder/", views.register_with_role_view, {"role": "responder"}, name="register_responder"),
    path("pending-approval/", views.pending_approval_view, name="pending_approval"),
    path("logout/", views.logout_view, name="logout"),
    path("password-reset/", views.password_reset_request_view, name="password_reset"),
    path("password-reset/verify/", views.password_reset_verify_view, name="password_reset_verify"),
    path("password-reset/resend/", views.password_reset_resend_view, name="password_reset_resend"),
    path("password-reset/new-password/", views.password_reset_new_password_view, name="password_reset_new_password"),
    path("password-reset/complete/", views.password_reset_complete_view, name="password_reset_complete"),
    path("dashboard/", views.dashboard_home, name="dashboard_home"),
    path("dashboard/profile/", views.profile_edit_view, name="profile_edit"),
    path("dashboard/incidents/report/", views.report_incident_page, name="incident_report"),
    path("dashboard/incidents/report/emergency/", views.emergency_incident_report_page, name="incident_report_emergency"),
    path("dashboard/incidents/", views.incident_list_view, name="incident_list"),
    path("dashboard/incidents/history/", views.incident_history_view, name="incident_history"),
    path("dashboard/incidents/map/", views.incident_map_view, name="incident_map"),
    path("dashboard/incidents/map-data/", views.incident_map_data_view, name="incident_map_data"),
    path("dashboard/incidents/<int:incident_id>/", views.incident_detail_view, name="incident_detail"),
    path("dashboard/incidents/<int:incident_id>/status/", views.incident_status_update_view, name="incident_status_update"),
    path(
        "dashboard/incidents/<int:incident_id>/status-enquiry/request/",
        views.incident_status_enquiry_request_view,
        name="incident_status_enquiry_request",
    ),
    path(
        "dashboard/incidents/<int:incident_id>/status-enquiry/respond/",
        views.incident_status_enquiry_response_view,
        name="incident_status_enquiry_response",
    ),
    path(
        "dashboard/incidents/<int:incident_id>/status-enquiry/finalize/",
        views.incident_status_enquiry_finalize_view,
        name="incident_status_enquiry_finalize",
    ),
    path(
        "dashboard/incidents/<int:incident_id>/status-enquiry/overdue-action/",
        views.incident_status_enquiry_overdue_action_view,
        name="incident_status_enquiry_overdue_action",
    ),
    path("dashboard/incidents/<int:incident_id>/verify/", views.incident_verify_view, name="incident_verify"),
    path("dashboard/incidents/<int:incident_id>/reject/", views.incident_reject_view, name="incident_reject"),
    path("dashboard/incidents/<int:incident_id>/close/", views.incident_close_view, name="incident_close"),
    path("dashboard/incidents/<int:incident_id>/final-review/", views.incident_final_review_submit_view, name="incident_final_review_submit"),
    path("dashboard/incidents/<int:incident_id>/fake-report/", views.incident_fake_report_submit_view, name="incident_fake_report_submit"),
    path("dashboard/incidents/<int:incident_id>/accept/", views.assignment_accept_view, name="assignment_accept"),
    path("dashboard/incidents/<int:incident_id>/decline/", views.assignment_decline_view, name="assignment_decline"),
    path("dashboard/notifications/", views.notification_list_view, name="notification_list"),
    path("dashboard/notifications/feed/", views.notification_feed_view, name="notification_feed"),
    path("dashboard/notifications/mark-all-read/", views.notification_mark_all_read_view, name="notification_mark_all_read"),
    path("dashboard/notifications/<int:notification_id>/mark-read/", views.notification_mark_read_view, name="notification_mark_read"),
    path("dashboard/responders/rankings/", views.responder_rankings_view, name="responder_rankings"),
    path("dashboard/fake-reports/", views.fake_reports_review_list_view, name="fake_reports_review"),
    path("dashboard/fake-reports/history/", views.fake_reports_history_view, name="fake_reports_history"),
    path("dashboard/fake-reports/<int:report_id>/action/", views.fake_report_action_view, name="fake_report_action"),
    path("dashboard/verifications/", views.verification_list_view, name="verification_list"),
    path("dashboard/verifications/<int:user_id>/approve/", views.approve_user_view, name="approve_user"),
    path("dashboard/verifications/<int:user_id>/reject/", views.reject_user_view, name="reject_user"),
    path(
        "dashboard/verifications/upgrades/<int:upgrade_request_id>/approve/",
        views.approve_responder_upgrade_view,
        name="approve_responder_upgrade",
    ),
    path(
        "dashboard/verifications/upgrades/<int:upgrade_request_id>/reject/",
        views.reject_responder_upgrade_view,
        name="reject_responder_upgrade",
    ),

    # --- India-wide features ---
    path("dashboard/india-map/", dv.india_map_view, name="india_map"),
    path("dashboard/india-map/data/", dv.india_map_data_view, name="india_map_data"),
    path("dashboard/national/", dv.national_dashboard_view, name="national_dashboard"),

    # Risk Zones
    path("dashboard/risk-zones/", dv.risk_zone_list_view, name="risk_zone_list"),
    path("dashboard/risk-zones/create/", dv.risk_zone_create_view, name="risk_zone_create"),
    path("dashboard/risk-zones/<int:zone_id>/", dv.risk_zone_detail_view, name="risk_zone_detail"),
    path("dashboard/risk-zones/<int:zone_id>/mitigation/create/", dv.mitigation_task_create_view, name="mitigation_task_create"),
    path("dashboard/risk-zones/mitigation/<int:task_id>/update/", dv.mitigation_task_update_view, name="mitigation_task_update"),

    # Early Warnings
    path("dashboard/warnings/", dv.warning_list_view, name="warning_list"),
    path("dashboard/warnings/create/", dv.warning_create_view, name="warning_create"),
    path("dashboard/warnings/<int:warning_id>/", dv.warning_detail_view, name="warning_detail"),
    path("dashboard/warnings/<int:warning_id>/deactivate/", dv.warning_deactivate_view, name="warning_deactivate"),

    # Shelters
    path("dashboard/shelters/", dv.shelter_list_view, name="shelter_list"),
    path("dashboard/shelters/create/", dv.shelter_create_view, name="shelter_create"),
    path("dashboard/shelters/<int:shelter_id>/", dv.shelter_detail_view, name="shelter_detail"),
    path("dashboard/shelters/<int:shelter_id>/occupancy/", dv.shelter_update_occupancy_view, name="shelter_update_occupancy"),

    # Evacuation Plans
    path("dashboard/evacuation/", dv.evacuation_plan_list_view, name="evacuation_list"),
    path("dashboard/evacuation/create/", dv.evacuation_plan_create_view, name="evacuation_create"),
    path("dashboard/evacuation/<int:plan_id>/", dv.evacuation_plan_detail_view, name="evacuation_detail"),
    path("dashboard/evacuation/<int:plan_id>/activate/", dv.evacuation_plan_activate_view, name="evacuation_activate"),

    # Relief Resources
    path("dashboard/resources/", dv.resource_list_view, name="resource_list"),
    path("dashboard/resources/request/", dv.resource_request_create_view, name="resource_request_create"),
    path("dashboard/resources/requests/", dv.resource_request_list_view, name="resource_requests"),
    path("dashboard/resources/requests/<int:req_id>/action/", dv.resource_request_action_view, name="resource_request_action"),

    # Damage Assessments
    path("dashboard/damage-assessments/", dv.damage_assessment_list_view, name="damage_assessment_list"),
    path("dashboard/incidents/<int:incident_id>/damage-assessment/", dv.damage_assessment_create_view, name="damage_assessment_create"),
    path("dashboard/damage-assessments/<int:assessment_id>/", dv.damage_assessment_detail_view, name="damage_assessment_detail"),
    path("dashboard/damage-assessments/<int:assessment_id>/verify/", dv.damage_assessment_verify_view, name="damage_assessment_verify"),

    # Infrastructure
    path("dashboard/infrastructure/", dv.infrastructure_list_view, name="infrastructure_list"),
    path("dashboard/infrastructure/create/", dv.infrastructure_create_view, name="infrastructure_create"),
    path("dashboard/infrastructure/map-data/", dv.infrastructure_map_data_view, name="infrastructure_map_data"),

    # AJAX
    path("api/districts/", dv.districts_by_state_view, name="districts_by_state"),

    # Subscriptions
    path("dashboard/subscriptions/", dv.my_subscriptions_view, name="my_subscriptions"),
    path("dashboard/subscriptions/subscribe/", dv.subscribe_location_view, name="subscribe_location"),
    path("dashboard/subscriptions/<int:sub_id>/unsubscribe/", dv.unsubscribe_location_view, name="unsubscribe_location"),

    path("api/", include("diaster_management_app.api_urls")),
]