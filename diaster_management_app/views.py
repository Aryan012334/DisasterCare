import secrets
from urllib.parse import urlencode
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.forms import PasswordChangeForm
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    EmergencyIncidentReportForm,
    IncidentFakeReportForm,
    IncidentFinalReviewForm,
    PasswordResetOTPRequestForm,
    PasswordResetOTPVerifyForm,
    PasswordResetSetPasswordForm,
    IncidentReportForm,
    IncidentStatusEnquiryFinalizeForm,
    IncidentStatusEnquiryOverdueActionForm,
    IncidentStatusEnquiryRequestForm,
    IncidentStatusEnquiryResponseForm,
    IncidentStatusUpdateForm,
    ResponderProfileForm,
    ResponderUpgradeRequestForm,
    UserProfileUpdateForm,
    UserRegistrationForm,
    UserUpdateForm,
)
from .models import (
    Incident,
    IncidentAssignment,
    IncidentFakeReport,
    IncidentFakeReportMedia,
    IncidentFinalReview,
    IncidentFinalReviewMedia,
    IncidentRiskPrediction,
    IncidentMedia,
    IncidentStatusEnquiry,
    IncidentUpdate,
    NotificationLog,
    PasswordResetOTP,
    ResponderProfile,
    ResponderUpgradeRequest,
    UserProfile,
)
from .services.incidents import build_incident_payload
from .services.risk_alerts import build_time_series_predictions, generate_consequence_warnings


def _form_error_message(form, fallback="Please check the form and try again."):
    issues = []
    for field, errors in form.errors.items():
        if field == "__all__":
            label = "Form"
        else:
            label = form.fields[field].label or field.replace("_", " ").title()
        issues.append(f"{label}: {errors[0]}")
        if len(issues) == 3:
            break
    return "Please fix: " + " | ".join(issues) if issues else fallback


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def _log_notification(*, incident, user, target_type, subject, message):
    NotificationLog.objects.create(
        incident=incident,
        user=user,
        target_type=target_type,
        subject=subject,
        message=message,
        delivered=True,
    )


def _notify_admins(*, incident, subject, message):
    admins = get_user_model().objects.filter(is_superuser=True, is_active=True)
    for admin in admins:
        _log_notification(
            incident=incident,
            user=admin,
            target_type=NotificationLog.TargetType.ADMIN,
            subject=subject,
            message=message,
        )


def _reporter_status_label(status):
    labels = {
        Incident.Status.PENDING: "Waiting for review",
        Incident.Status.VERIFIED: "Approved",
        Incident.Status.REJECTED: "Not approved",
        Incident.Status.DISPATCHED: "Team notified",
        Incident.Status.ON_SCENE: "Team on site",
        Incident.Status.STABILIZED: "Situation stable",
        Incident.Status.ESCALATED: "Extra support requested",
        Incident.Status.CLOSED: "Closed",
    }
    return labels.get(status, str(status).replace("_", " ").title())


def _reporter_safety_message(incident):
    if incident.incident_type == Incident.IncidentType.FIRE:
        return "Smoke may spread nearby. Stay indoors if possible and avoid the area."
    if incident.incident_type == Incident.IncidentType.FLOOD:
        return "Flood risk may increase nearby. Stay away from low roads and standing water."
    if incident.incident_type == Incident.IncidentType.EARTHQUAKE:
        return "Aftershocks may happen nearby. Stay away from damaged buildings and poles."
    if incident.incident_type == Incident.IncidentType.LANDSLIDE:
        return "Ground may still be unstable nearby. Keep away from slopes and blocked roads."
    if incident.incident_type == Incident.IncidentType.ROAD_ACCIDENT:
        return "Traffic risk is higher nearby. Avoid the area until routes clear."
    if incident.incident_type == Incident.IncidentType.MEDICAL:
        return "Emergency activity is ongoing nearby. Keep routes clear for emergency vehicles."
    return "Please stay away from the affected area and follow official local instructions."


def _build_safety_notification_content(*, incident, target_type, warning_line, top_forecast):
    if target_type == NotificationLog.TargetType.REPORTER:
        subject = f"Safety update near you: {incident.get_incident_type_display()} #{incident.id}"
        return subject, _reporter_safety_message(incident)

    if target_type == NotificationLog.TargetType.RESPONDER:
        subject = f"Responder safety alert: {incident.get_incident_type_display()} #{incident.id}"
        message = warning_line or "Safety risk is active near the incident area."
        if top_forecast:
            message += f" Expected risk in next {top_forecast['horizon_hours']}h: {top_forecast['risk_level'].title()}."
        message += " Stay ready for assignment updates."
        return subject, message

    subject = f"Safety warning near you: {incident.get_incident_type_display()} #{incident.id}"
    risk_line = ""
    if top_forecast:
        risk_line = (
            f" Risk in next {top_forecast['horizon_hours']}h: "
            f"{top_forecast['risk_level'].title()} ({top_forecast['risk_score']})."
        )
    return subject, (warning_line or "Please avoid the affected zone and follow official instructions.") + risk_line


def _accepted_assignment(user, incident):
    return incident.assignments.filter(
        responder=user,
        status=IncidentAssignment.AssignmentStatus.ACCEPTED,
    ).first()


def _active_committed_assignment(user, *, exclude_incident_id=None):
    query = IncidentAssignment.objects.select_related("incident").filter(
        responder=user,
        status=IncidentAssignment.AssignmentStatus.ACCEPTED,
    ).exclude(
        incident__status__in={Incident.Status.REJECTED, Incident.Status.CLOSED},
    )
    if exclude_incident_id is not None:
        query = query.exclude(incident_id=exclude_incident_id)
    return query.order_by("-accepted_at", "-created_at").first()


def _responder_allowed_for_incident(user, incident):
    if user.profile.role != UserProfile.Role.RESPONDER:
        return False
    return _accepted_assignment(user, incident) is not None


def _response_points_for_incident(incident):
    base_map = {
        Incident.Severity.LOW: 15,
        Incident.Severity.MEDIUM: 25,
        Incident.Severity.HIGH: 40,
        Incident.Severity.CRITICAL: 60,
    }
    points = base_map.get(incident.severity, 20)
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        points += 15
    if incident.dispatched_at and incident.first_response_at:
        response_minutes = (incident.first_response_at - incident.dispatched_at).total_seconds() / 60
        if response_minutes <= 20:
            points += 20
        elif response_minutes <= 45:
            points += 10
    return points


def _responder_status_steps(incident):
    steps = [
        {"value": Incident.Status.ON_SCENE, "label": "On Scene"},
        {"value": Incident.Status.STABILIZED, "label": "Stabilized"},
        {"value": Incident.Status.ESCALATED, "label": "Escalated"},
    ]
    transition_map = {
        Incident.Status.VERIFIED: {Incident.Status.ON_SCENE},
        Incident.Status.DISPATCHED: {Incident.Status.ON_SCENE},
        Incident.Status.ON_SCENE: {Incident.Status.STABILIZED, Incident.Status.ESCALATED},
        Incident.Status.STABILIZED: {Incident.Status.ESCALATED},
        Incident.Status.ESCALATED: {Incident.Status.STABILIZED},
    }
    allowed_next = transition_map.get(incident.status, set())
    return steps, allowed_next


def _closure_requirements(incident, final_review):
    checks = [
        (
            "status_stabilized",
            "Incident status is Stabilized",
            incident.status == Incident.Status.STABILIZED,
        ),
        (
            "final_review_submitted",
            "Responder submitted final review",
            final_review is not None,
        ),
        (
            "proof_attached",
            "Final review includes proof attachment",
            bool(final_review and final_review.media.exists()),
        ),
        (
            "responder_recommended_close",
            "Responder recommended closure",
            bool(final_review and final_review.recommend_close),
        ),
    ]
    missing_labels = [label for _, label, done in checks if not done]
    return all(done for _, _, done in checks), checks, missing_labels


def _refresh_risk_predictions(incident):
    points = build_time_series_predictions(incident)
    IncidentRiskPrediction.objects.filter(incident=incident).delete()
    IncidentRiskPrediction.objects.bulk_create(
        [
            IncidentRiskPrediction(
                incident=incident,
                horizon_hours=item["horizon_hours"],
                forecast_for=item["forecast_for"],
                risk_score=item["risk_score"],
                risk_level=item["risk_level"],
                advisory=item["advisory"],
            )
            for item in points
        ]
    )
    return points


def _resolve_nearby_user_targets(incident, radius_km):
    radius_km = max(2.0, float(radius_km))
    targets = {}

    admins = get_user_model().objects.filter(is_superuser=True, is_active=True)
    for user in admins:
        if hasattr(user, "profile") and user.profile.is_blocked:
            continue
        targets[user.id] = (user, NotificationLog.TargetType.ADMIN)

    responders = (
        ResponderProfile.objects.select_related("user")
        .filter(
            user__is_active=True,
            user__profile__verification_status=UserProfile.VerificationStatus.APPROVED,
            user__profile__is_blocked=False,
        )
        .exclude(latitude__isnull=True)
        .exclude(longitude__isnull=True)
    )
    for responder in responders:
        distance = _haversine_km(
            float(incident.latitude),
            float(incident.longitude),
            float(responder.latitude),
            float(responder.longitude),
        )
        if distance <= radius_km:
            targets[responder.user_id] = (responder.user, NotificationLog.TargetType.RESPONDER)

    reporter_locations = {}
    reporter_reports = (
        Incident.objects.select_related("reporter")
        .filter(
            reporter__isnull=False,
            reporter__is_active=True,
            reporter__profile__role=UserProfile.Role.REPORTER,
            reporter__profile__verification_status=UserProfile.VerificationStatus.APPROVED,
            reporter__profile__is_blocked=False,
            created_at__gte=timezone.now() - timedelta(days=45),
        )
        .order_by("-created_at")
    )
    for row in reporter_reports:
        if row.reporter_id not in reporter_locations:
            reporter_locations[row.reporter_id] = (float(row.latitude), float(row.longitude), row.reporter)

    for reporter_id, (lat, lon, reporter_user) in reporter_locations.items():
        distance = _haversine_km(
            float(incident.latitude),
            float(incident.longitude),
            lat,
            lon,
        )
        if distance <= radius_km:
            targets[reporter_id] = (reporter_user, NotificationLog.TargetType.REPORTER)

    return list(targets.values())


def _should_broadcast_consequence_warning(incident):
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        return True
    if incident.severity == Incident.Severity.CRITICAL:
        return True
    if incident.incident_type == Incident.IncidentType.FIRE:
        return (
            incident.severity in {Incident.Severity.HIGH, Incident.Severity.CRITICAL}
            or incident.people_affected >= 15
            or incident.affected_radius_km >= 2.0
        )
    return incident.severity == Incident.Severity.HIGH and incident.people_affected >= 8


def _run_consequence_intelligence_and_notify(incident):
    warnings = generate_consequence_warnings(incident)
    if warnings:
        incident.ai_consequence_summary = "\n".join(warnings)
        incident.save(update_fields=["ai_consequence_summary", "updated_at"])
    else:
        incident.ai_consequence_summary = ""
        incident.save(update_fields=["ai_consequence_summary", "updated_at"])

    predictions = _refresh_risk_predictions(incident)
    if not _should_broadcast_consequence_warning(incident):
        return warnings, predictions

    alert_radius_km = max(float(incident.affected_radius_km) * 1.8, 5.0)
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        alert_radius_km = max(alert_radius_km, 10.0)
    targets = _resolve_nearby_user_targets(incident, alert_radius_km)
    if not targets:
        return warnings, predictions

    top_forecast = predictions[0] if predictions else None
    warning_line = warnings[0] if warnings else "Please avoid the affected zone and follow official instructions."
    for user, target_type in targets:
        subject, message = _build_safety_notification_content(
            incident=incident,
            target_type=target_type,
            warning_line=warning_line,
            top_forecast=top_forecast,
        )
        exists = NotificationLog.objects.filter(incident=incident, user=user, subject=subject).exists()
        if exists:
            continue
        _log_notification(
            incident=incident,
            user=user,
            target_type=target_type,
            subject=subject,
            message=message,
        )
    return warnings, predictions


def _responder_ready(user):
    if user.profile.role != UserProfile.Role.RESPONDER:
        return True, ""
    responder = getattr(user, "responder_profile", None)
    if responder is None:
        return False, "Complete responder profile with location and availability."
    if not responder.is_available:
        return False, "Responder availability is off. Turn it on to receive assignments."
    if responder.latitude is None or responder.longitude is None:
        return False, "Responder location is missing. Update latitude and longitude."
    if not responder.last_checkin_at or responder.last_checkin_at.date() != timezone.localdate():
        return False, "Daily responder check-in is pending. Update availability and location today."
    return True, ""


def _validate_on_scene_location(
    user,
    incident,
    *,
    live_latitude_raw="",
    live_longitude_raw="",
    require_live_location=False,
):
    responder = getattr(user, "responder_profile", None)
    if responder is None:
        return (
            False,
            "On Scene check failed: responder profile is missing. Complete responder setup first.",
            None,
            None,
            None,
        )

    lat_raw = (live_latitude_raw or "").strip()
    lon_raw = (live_longitude_raw or "").strip()
    has_live_lat = bool(lat_raw)
    has_live_lon = bool(lon_raw)

    lat_value = responder.latitude
    lon_value = responder.longitude
    if has_live_lat or has_live_lon:
        if not has_live_lat or not has_live_lon:
            return (
                False,
                "On Scene check failed: live location data is incomplete. Allow location access and retry.",
                None,
                None,
                None,
            )
        try:
            lat_value = float(lat_raw)
            lon_value = float(lon_raw)
        except (TypeError, ValueError):
            return False, "On Scene check failed: live location is invalid. Retry with GPS enabled.", None, None, None
    elif require_live_location:
        return (
            False,
            "On Scene check failed: live location is required. Enable location permission and try again.",
            None,
            None,
            None,
        )

    if lat_value is None or lon_value is None:
        return False, "On Scene check failed: responder location is missing. Update your location and try again.", None, None, None
    if float(lat_value) < -90 or float(lat_value) > 90:
        return False, "On Scene check failed: latitude is out of valid range.", None, None, None
    if float(lon_value) < -180 or float(lon_value) > 180:
        return False, "On Scene check failed: longitude is out of valid range.", None, None, None

    distance_km = _haversine_km(
        float(incident.latitude),
        float(incident.longitude),
        float(lat_value),
        float(lon_value),
    )
    if distance_km > ON_SCENE_MAX_DISTANCE_KM:
        return (
            False,
            (
                f"On Scene check failed: you are {distance_km:.2f} km away from incident location. "
                f"Move within {ON_SCENE_MAX_DISTANCE_KM:.1f} km to mark On Scene."
            ),
            distance_km,
            float(lat_value),
            float(lon_value),
        )
    return True, "", distance_km, float(lat_value), float(lon_value)


def _rank_available_responders_by_distance(incident):
    candidates = (
        ResponderProfile.objects.select_related("user")
        .filter(
            is_available=True,
            user__is_active=True,
            user__profile__verification_status=UserProfile.VerificationStatus.APPROVED,
            last_checkin_at__date=timezone.localdate(),
        )
        .exclude(latitude__isnull=True)
        .exclude(longitude__isnull=True)
    )
    ranked = []
    for responder in candidates:
        distance = _haversine_km(
            float(incident.latitude),
            float(incident.longitude),
            float(responder.latitude),
            float(responder.longitude),
        )
        ranked.append((distance, responder))
    ranked.sort(key=lambda row: row[0])
    return ranked


def _notify_nearest_responders_for_normal_incident(incident, limit=1):
    sent = 0
    for distance, responder in _rank_available_responders_by_distance(incident)[:limit]:
        subject = f"Normal incident #{incident.id} near your area"
        if NotificationLog.objects.filter(incident=incident, user=responder.user, subject=subject).exists():
            continue
        _log_notification(
            incident=incident,
            user=responder.user,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=subject,
            message=(
                f"A normal incident '{incident.title}' was reported ({distance:.2f} km away). "
                "Await admin verification before response assignment."
            ),
        )
        sent += 1
    return sent


def _dispatch_nearest_responders(incident, limit=5):
    created = 0
    for distance, responder in _rank_available_responders_by_distance(incident)[:limit]:
        assignment, was_created = IncidentAssignment.objects.get_or_create(
            incident=incident,
            responder=responder.user,
            defaults={"distance_km": distance, "status": IncidentAssignment.AssignmentStatus.NOTIFIED},
        )
        if was_created:
            created += 1
            _log_notification(
                incident=incident,
                user=responder.user,
                target_type=NotificationLog.TargetType.RESPONDER,
                subject=f"New incident #{incident.id}",
                message=f"You are requested for incident '{incident.title}' ({distance:.2f} km away).",
            )
    return created


def _dispatch_emergency_with_fallback(incident, *, nearby_radius_km=10.0, limit=8):
    ranked = _rank_available_responders_by_distance(incident)
    if not ranked:
        return 0, False

    selected = [(distance, responder) for distance, responder in ranked if distance <= nearby_radius_km][:limit]
    used_fallback = False
    if not selected:
        selected = [ranked[0]]
        used_fallback = True

    created = 0
    for distance, responder in selected:
        assignment, was_created = IncidentAssignment.objects.get_or_create(
            incident=incident,
            responder=responder.user,
            defaults={"distance_km": distance, "status": IncidentAssignment.AssignmentStatus.NOTIFIED},
        )
        if not was_created:
            continue
        created += 1
        message = f"You are requested for incident '{incident.title}' ({distance:.2f} km away)."
        if used_fallback:
            message = (
                f"No nearby responder was available for emergency incident '{incident.title}'. "
                f"You are the closest available responder ({distance:.2f} km away)."
            )
        _log_notification(
            incident=incident,
            user=responder.user,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=f"New incident #{incident.id}",
            message=message,
        )
    return created, used_fallback


def _dispatch_escalation_support(incident, *, radius_km=30.0):
    assignments_opened = 0
    notifications_sent = 0
    subject = f"Escalated incident #{incident.id}"
    for distance, responder in _rank_available_responders_by_distance(incident):
        if distance > radius_km:
            break
        assignment, was_created = IncidentAssignment.objects.get_or_create(
            incident=incident,
            responder=responder.user,
            defaults={"distance_km": distance, "status": IncidentAssignment.AssignmentStatus.NOTIFIED},
        )
        assignment_opened = was_created
        if not was_created and assignment.status == IncidentAssignment.AssignmentStatus.DECLINED:
            # Declined assignments are locked for that responder and should not be re-opened.
            continue
        if not was_created and assignment.status == IncidentAssignment.AssignmentStatus.CANCELLED:
            assignment.status = IncidentAssignment.AssignmentStatus.NOTIFIED
            assignment.accepted_at = None
            assignment.declined_at = None
            assignment.distance_km = distance
            assignment.save(update_fields=["status", "accepted_at", "declined_at", "distance_km", "updated_at"])
            assignment_opened = True
        elif not was_created and assignment.distance_km != distance:
            assignment.distance_km = distance
            assignment.save(update_fields=["distance_km", "updated_at"])

        if assignment_opened:
            assignments_opened += 1

        if NotificationLog.objects.filter(incident=incident, user=responder.user, subject=subject).exists():
            continue
        _log_notification(
            incident=incident,
            user=responder.user,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=subject,
            message=(
                f"Incident '{incident.title}' was escalated. Additional responder support is requested "
                f"({distance:.2f} km away)."
            ),
        )
        notifications_sent += 1
    return assignments_opened, notifications_sent


def _offer_incident_to_next_responders(incident, limit=3, exclude_user_ids=None):
    excluded_ids = set(exclude_user_ids or [])
    assigned_ids = set(incident.assignments.values_list("responder_id", flat=True))
    blocked_ids = assigned_ids.union(excluded_ids)

    created = 0
    for distance, responder in _rank_available_responders_by_distance(incident):
        if responder.user_id in blocked_ids:
            continue
        IncidentAssignment.objects.create(
            incident=incident,
            responder=responder.user,
            distance_km=distance,
            status=IncidentAssignment.AssignmentStatus.NOTIFIED,
        )
        _log_notification(
            incident=incident,
            user=responder.user,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=f"New incident #{incident.id}",
            message=f"You are requested for incident '{incident.title}' ({distance:.2f} km away).",
        )
        created += 1
        blocked_ids.add(responder.user_id)
        if created >= limit:
            break
    return created


def _apply_triage_to_incident(incident, description, allow_lower_severity=True):
    triage = build_incident_payload(description)
    incident.ai_confidence = triage["ai_confidence"]
    incident.ai_risk_score = triage["ai_risk_score"]
    if incident.incident_type == Incident.IncidentType.OTHER and triage["incident_type"] in dict(Incident.IncidentType.choices):
        incident.incident_type = triage["incident_type"]
    triage_severity = triage["severity"]
    if triage_severity in dict(Incident.Severity.choices):
        if allow_lower_severity or triage_severity in {Incident.Severity.HIGH, Incident.Severity.CRITICAL}:
            incident.severity = triage_severity


def _find_duplicate_candidate(incident):
    return (
        Incident.objects.filter(
            incident_type=incident.incident_type,
            created_at__gte=timezone.now() - timedelta(hours=2),
            latitude__gte=float(incident.latitude) - 0.01,
            latitude__lte=float(incident.latitude) + 0.01,
            longitude__gte=float(incident.longitude) - 0.01,
            longitude__lte=float(incident.longitude) + 0.01,
        )
        .exclude(id=incident.id)
        .order_by("-created_at")
        .first()
    )


ACTIVE_INCIDENT_STATUSES = {
    Incident.Status.PENDING,
    Incident.Status.VERIFIED,
    Incident.Status.DISPATCHED,
    Incident.Status.ON_SCENE,
    Incident.Status.STABILIZED,
    Incident.Status.ESCALATED,
}
HISTORY_INCIDENT_STATUSES = {
    Incident.Status.REJECTED,
    Incident.Status.CLOSED,
}

DAILY_REPORT_LIMIT = 8
WINDOW_REPORT_LIMIT = 3
WINDOW_REPORT_MINUTES = 30
STALE_STATUS_ENQUIRY_DAYS = 3
ENQUIRY_RESPONSE_OVERDUE_HOURS = 24
ON_SCENE_MAX_DISTANCE_KM = 0.5
PASSWORD_RESET_OTP_LENGTH = max(4, int(getattr(settings, "PASSWORD_RESET_OTP_LENGTH", 6)))
PASSWORD_RESET_OTP_EXPIRY_SECONDS = max(60, int(getattr(settings, "PASSWORD_RESET_OTP_EXPIRY_SECONDS", 600)))
PASSWORD_RESET_OTP_MAX_ATTEMPTS = max(1, int(getattr(settings, "PASSWORD_RESET_OTP_MAX_ATTEMPTS", 5)))
PASSWORD_RESET_OTP_RESEND_COOLDOWN_SECONDS = max(
    0,
    int(getattr(settings, "PASSWORD_RESET_OTP_RESEND_COOLDOWN_SECONDS", 60)),
)
PASSWORD_RESET_VERIFIED_SESSION_SECONDS = max(
    60,
    int(getattr(settings, "PASSWORD_RESET_VERIFIED_SESSION_SECONDS", 900)),
)
PASSWORD_RESET_SESSION_USER_KEY = "password_reset_user_id"
PASSWORD_RESET_SESSION_VERIFIED_AT_KEY = "password_reset_verified_at"
PASSWORD_RESET_SESSION_EMAIL_KEY = "password_reset_email"


def _password_reset_expiry_minutes():
    return max(1, int(PASSWORD_RESET_OTP_EXPIRY_SECONDS / 60))


def _generate_numeric_otp(length=PASSWORD_RESET_OTP_LENGTH):
    return "".join(secrets.choice("0123456789") for _ in range(length))


def _password_reset_target_user(email):
    return get_user_model().objects.filter(email__iexact=email, is_active=True).first()


def _latest_active_password_reset_challenge(user):
    return PasswordResetOTP.objects.filter(user=user, is_used=False).order_by("-created_at", "-id").first()


def _issue_password_reset_challenge(user):
    now = timezone.now()
    challenge = _latest_active_password_reset_challenge(user)
    if challenge and challenge.expires_at > now and challenge.last_sent_at:
        elapsed = int((now - challenge.last_sent_at).total_seconds())
        if elapsed < PASSWORD_RESET_OTP_RESEND_COOLDOWN_SECONDS:
            wait_seconds = PASSWORD_RESET_OTP_RESEND_COOLDOWN_SECONDS - elapsed
            return None, None, max(1, wait_seconds)

    otp_code = _generate_numeric_otp()
    expires_at = now + timedelta(seconds=PASSWORD_RESET_OTP_EXPIRY_SECONDS)

    if challenge and challenge.expires_at > now:
        challenge.otp_hash = make_password(otp_code)
        challenge.expires_at = expires_at
        challenge.attempts_remaining = PASSWORD_RESET_OTP_MAX_ATTEMPTS
        challenge.send_count += 1
        challenge.last_sent_at = now
        challenge.save(
            update_fields=[
                "otp_hash",
                "expires_at",
                "attempts_remaining",
                "send_count",
                "last_sent_at",
                "updated_at",
            ]
        )
        return challenge, otp_code, 0

    challenge = PasswordResetOTP.objects.create(
        user=user,
        email=(user.email or "").strip().lower(),
        otp_hash=make_password(otp_code),
        expires_at=expires_at,
        attempts_remaining=PASSWORD_RESET_OTP_MAX_ATTEMPTS,
        send_count=1,
        last_sent_at=now,
    )
    return challenge, otp_code, 0


def _send_password_reset_otp_email(user, otp_code):
    expiry_minutes = _password_reset_expiry_minutes()
    context = {
        "user_display_name": user.get_full_name() or user.username or "User",
        "otp_code": otp_code,
        "expiry_minutes": expiry_minutes,
        "support_email": settings.DEFAULT_FROM_EMAIL,
    }
    subject = render_to_string("auth/password_reset_otp_subject.txt", context).strip()
    text_body = render_to_string("auth/password_reset_otp_email.txt", context)
    html_body = render_to_string("auth/password_reset_otp_email.html", context)
    email_message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    if html_body.strip():
        email_message.attach_alternative(html_body, "text/html")
    email_message.send(fail_silently=False)


def _password_reset_verify_url(email):
    query = urlencode({"email": email})
    return f"{reverse('disaster_management:password_reset_verify')}?{query}"


def _clear_password_reset_session(request):
    request.session.pop(PASSWORD_RESET_SESSION_USER_KEY, None)
    request.session.pop(PASSWORD_RESET_SESSION_VERIFIED_AT_KEY, None)
    request.session.pop(PASSWORD_RESET_SESSION_EMAIL_KEY, None)


def _set_password_reset_verified_session(request, user, email):
    request.session[PASSWORD_RESET_SESSION_USER_KEY] = user.id
    request.session[PASSWORD_RESET_SESSION_VERIFIED_AT_KEY] = int(timezone.now().timestamp())
    request.session[PASSWORD_RESET_SESSION_EMAIL_KEY] = email


def _get_verified_password_reset_user(request):
    user_id = request.session.get(PASSWORD_RESET_SESSION_USER_KEY)
    verified_at_ts = request.session.get(PASSWORD_RESET_SESSION_VERIFIED_AT_KEY)
    if not user_id or verified_at_ts is None:
        return None

    age_seconds = int(timezone.now().timestamp()) - int(verified_at_ts)
    if age_seconds > PASSWORD_RESET_VERIFIED_SESSION_SECONDS:
        _clear_password_reset_session(request)
        return None

    user = get_user_model().objects.filter(id=user_id, is_active=True).first()
    if user is None:
        _clear_password_reset_session(request)
    return user


def _parse_date_or_none(raw):
    value = (raw or "").strip()
    if not value:
        return None


def _incident_last_update_at(incident):
    latest_update = incident.updates.order_by("-created_at").values_list("created_at", flat=True).first()
    return latest_update or incident.updated_at or incident.created_at


def _is_incident_stale_for_enquiry(incident):
    last_update_at = _incident_last_update_at(incident)
    if not last_update_at:
        return False, 0, None
    elapsed = timezone.now() - last_update_at
    return elapsed >= timedelta(days=STALE_STATUS_ENQUIRY_DAYS), max(0, elapsed.days), last_update_at


def _open_status_enquiry(incident):
    return (
        incident.status_enquiries.select_related("requested_by", "responded_by", "finalized_by", "overdue_action_by")
        .filter(status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED)
        .order_by("-requested_at", "-created_at")
        .first()
    )


def _latest_status_enquiry(incident):
    return (
        incident.status_enquiries.select_related("requested_by", "responded_by", "finalized_by", "overdue_action_by")
        .order_by("-requested_at", "-created_at")
        .first()
    )


def _is_enquiry_overdue(enquiry):
    if enquiry is None:
        return False
    if enquiry.status != IncidentStatusEnquiry.WorkflowStatus.REQUESTED:
        return False
    cutoff = timezone.now() - timedelta(hours=ENQUIRY_RESPONSE_OVERDUE_HOURS)
    return enquiry.requested_at <= cutoff


def _notify_responders_for_status_enquiry(incident, note_text):
    responders = (
        incident.assignments.select_related("responder")
        .filter(status=IncidentAssignment.AssignmentStatus.ACCEPTED)
        .order_by("-accepted_at", "-created_at")
    )
    if not responders.exists():
        responders = (
            incident.assignments.select_related("responder")
            .filter(status=IncidentAssignment.AssignmentStatus.NOTIFIED)
            .order_by("-created_at")
        )
    sent = 0
    for row in responders:
        subject = f"Status enquiry requested for incident #{incident.id}"
        if NotificationLog.objects.filter(incident=incident, user=row.responder, subject=subject, is_read=False).exists():
            continue
        message = (
            "Admin requested your current status update. "
            "Open incident details and submit enquiry response."
        )
        if note_text:
            message += f" Note: {note_text}"
        _log_notification(
            incident=incident,
            user=row.responder,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=subject,
            message=message,
        )
        sent += 1
    return sent


def _primary_responder_for_enquiry(incident):
    accepted_row = (
        incident.assignments.select_related("responder")
        .filter(status=IncidentAssignment.AssignmentStatus.ACCEPTED)
        .order_by("-accepted_at", "-created_at")
        .first()
    )
    if accepted_row:
        return accepted_row.responder
    notified_row = (
        incident.assignments.select_related("responder")
        .filter(status=IncidentAssignment.AssignmentStatus.NOTIFIED)
        .order_by("-created_at")
        .first()
    )
    if notified_row:
        return notified_row.responder
    return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _report_quota_snapshot(user):
    day_start = timezone.localdate()
    window_start = timezone.now() - timedelta(minutes=WINDOW_REPORT_MINUTES)
    daily_count = Incident.objects.filter(reporter=user, created_at__date=day_start).count()
    window_count = Incident.objects.filter(reporter=user, created_at__gte=window_start).count()
    return {
        "daily_count": daily_count,
        "daily_remaining": max(0, DAILY_REPORT_LIMIT - daily_count),
        "window_count": window_count,
        "window_remaining": max(0, WINDOW_REPORT_LIMIT - window_count),
    }


def _enforce_reporting_limits(user):
    if user.is_superuser:
        return True, ""
    stats = _report_quota_snapshot(user)
    if stats["daily_count"] >= DAILY_REPORT_LIMIT:
        return (
            False,
            f"Daily incident limit reached ({DAILY_REPORT_LIMIT}/day). Please wait until tomorrow.",
        )
    if stats["window_count"] >= WINDOW_REPORT_LIMIT:
        return (
            False,
            (
                f"Too many reports in short time ({WINDOW_REPORT_LIMIT} within {WINDOW_REPORT_MINUTES} minutes). "
                "Please wait before submitting another incident."
            ),
        )
    return True, ""


@require_GET
def health_check(request):
    return JsonResponse(
        {
            "service": "rdms",
            "status": "ok",
            "module": "diaster_management_app",
        }
    )


@require_GET
def dashboard_home(request):
    if not request.user.is_authenticated:
        return redirect("disaster_management:login")
    is_admin = bool(request.user.is_superuser)
    role_label = "Admin" if is_admin else request.user.profile.get_role_display()
    is_responder = not is_admin and request.user.profile.role == UserProfile.Role.RESPONDER
    is_reporter = not is_admin and request.user.profile.role == UserProfile.Role.REPORTER

    responder_profile = None
    show_responder_checkin_popup = False
    responder_checkin_reason = ""
    responder_is_available = False
    responder_latitude = ""
    responder_longitude = ""

    if is_responder:
        responder_profile = getattr(request.user, "responder_profile", None)
        if responder_profile:
            responder_is_available = bool(responder_profile.is_available)
            responder_latitude = responder_profile.latitude if responder_profile.latitude is not None else ""
            responder_longitude = responder_profile.longitude if responder_profile.longitude is not None else ""
            location_missing = responder_profile.latitude is None or responder_profile.longitude is None
            has_today_checkin = bool(
                responder_profile.last_checkin_at and responder_profile.last_checkin_at.date() == timezone.localdate()
            )
            if not has_today_checkin:
                show_responder_checkin_popup = True
                responder_checkin_reason = "Daily responder check-in is pending. Update availability and location."
                if responder_profile.is_available and location_missing:
                    responder_checkin_reason = "Location is required while availability is on."
            elif responder_profile.is_available and location_missing:
                show_responder_checkin_popup = True
                responder_checkin_reason = "Location is required while availability is on."
        else:
            show_responder_checkin_popup = True
            responder_checkin_reason = "Set your availability and location to continue as responder."

    if not is_admin:
        ready, reason = _responder_ready(request.user)
        if is_responder and not ready and (responder_profile is None or responder_profile.is_available):
            messages.warning(request, reason)
    profile_photo_url = None
    if hasattr(request.user, "profile") and request.user.profile.profile_photo:
        profile_photo_url = request.user.profile.profile_photo.url

    base_query = Incident.objects.all()
    if is_reporter:
        base_query = base_query.filter(reporter=request.user)

    today = timezone.localdate()
    today_reports = base_query.filter(created_at__date=today).count()
    active_alerts = base_query.filter(
        status__in=[
            Incident.Status.PENDING,
            Incident.Status.VERIFIED,
            Incident.Status.DISPATCHED,
            Incident.Status.ON_SCENE,
            Incident.Status.STABILIZED,
            Incident.Status.ESCALATED,
        ]
    ).count()
    total_responders = UserProfile.objects.filter(role=UserProfile.Role.RESPONDER, verification_status=UserProfile.VerificationStatus.APPROVED).count()
    resolved_count = base_query.filter(status=Incident.Status.CLOSED).count()
    verified_qs = base_query.filter(verified_at__isnull=False)
    dispatched_qs = base_query.filter(dispatched_at__isnull=False)
    avg_verify_minutes = 0
    avg_dispatch_minutes = 0
    if verified_qs.exists():
        total_verify = sum((row.verified_at - row.created_at).total_seconds() for row in verified_qs)
        avg_verify_minutes = round(total_verify / verified_qs.count() / 60, 1)
    if dispatched_qs.exists():
        total_dispatch = sum((row.dispatched_at - row.created_at).total_seconds() for row in dispatched_qs)
        avg_dispatch_minutes = round(total_dispatch / dispatched_qs.count() / 60, 1)

    my_response_points = None
    my_rank = None
    if hasattr(request.user, "responder_profile"):
        responder_profile = request.user.responder_profile
        my_response_points = responder_profile.response_points
        my_rank = (
            ResponderProfile.objects.filter(response_points__gt=responder_profile.response_points, user__is_active=True).count() + 1
        )
    top_responders = (
        ResponderProfile.objects.select_related("user")
        .filter(user__is_active=True, user__profile__verification_status=UserProfile.VerificationStatus.APPROVED)
        .order_by("-response_points", "-completed_incidents", "full_name")[:5]
    )
    unread_notifications = NotificationLog.objects.filter(user=request.user, is_read=False).count()

    dashboard_intro_title = "My Dashboard"
    dashboard_intro_text = "Track incidents, updates, and notifications."
    dashboard_quick_actions = []
    dashboard_focus_items = []
    dashboard_role_theme = "reporter"

    my_open_reports = 0
    my_closed_reports = 0
    my_pending_review_reports = 0
    my_new_assignment_requests = 0
    my_active_assignments = 0
    my_completed_tasks = 0
    pending_incidents = 0
    pending_user_verifications = 0
    open_status_enquiries = 0

    if is_admin:
        dashboard_role_theme = "admin"
        dashboard_intro_title = "Admin Command Center"
        dashboard_intro_text = "Monitor incoming reports, responder activity, and pending reviews."
        dashboard_quick_actions = [
            {"label": "Review Verifications", "url": reverse("disaster_management:verification_list")},
            {"label": "Review Fake Reports", "url": reverse("disaster_management:fake_reports_review")},
            {"label": "Open Incident Queue", "url": reverse("disaster_management:incident_list")},
            {"label": "Open Notifications", "url": reverse("disaster_management:notification_list")},
        ]
        pending_incidents = Incident.objects.filter(status=Incident.Status.PENDING).count()
        pending_user_verifications = UserProfile.objects.filter(
            role=UserProfile.Role.RESPONDER,
            verification_status=UserProfile.VerificationStatus.PENDING,
            user__is_superuser=False,
        ).count()
        open_status_enquiries = IncidentStatusEnquiry.objects.filter(
            status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED
        ).count()
        dashboard_focus_items = [
            f"{pending_incidents} incident(s) are waiting for admin review.",
            f"{pending_user_verifications} responder sign-up request(s) are pending.",
            f"{open_status_enquiries} status enquiry workflow(s) are still open.",
        ]
    elif is_responder:
        dashboard_role_theme = "responder"
        dashboard_intro_title = "Responder Mission Board"
        dashboard_intro_text = "See new task requests, active commitments, and your performance."
        dashboard_quick_actions = [
            {"label": "Active Queue", "url": reverse("disaster_management:incident_list")},
            {"label": "Incident Map", "url": reverse("disaster_management:incident_map")},
            {"label": "Responder Rankings", "url": reverse("disaster_management:responder_rankings")},
            {"label": "Open Notifications", "url": reverse("disaster_management:notification_list")},
        ]
        assignment_query = IncidentAssignment.objects.filter(responder=request.user).exclude(
            incident__status__in={Incident.Status.REJECTED, Incident.Status.CLOSED}
        )
        my_new_assignment_requests = assignment_query.filter(status=IncidentAssignment.AssignmentStatus.NOTIFIED).count()
        my_active_assignments = assignment_query.filter(status=IncidentAssignment.AssignmentStatus.ACCEPTED).count()
        my_completed_tasks = responder_profile.completed_incidents if responder_profile else 0
        dashboard_focus_items = [
            f"{my_new_assignment_requests} new task request(s) are waiting for your action.",
            f"{my_active_assignments} active task(s) are currently assigned to you.",
            f"{my_completed_tasks} task(s) completed so far.",
        ]
    else:
        dashboard_role_theme = "reporter"
        dashboard_intro_title = "Reporter Activity Board"
        dashboard_intro_text = "Track the progress of your reports and latest safety updates."
        dashboard_quick_actions = [
            {"label": "Report Incident", "url": reverse("disaster_management:incident_report")},
            {"label": "Emergency Report", "url": reverse("disaster_management:incident_report_emergency")},
            {"label": "My Active Reports", "url": reverse("disaster_management:incident_list")},
            {"label": "Report History", "url": reverse("disaster_management:incident_history")},
        ]
        my_reports = Incident.objects.filter(reporter=request.user)
        my_open_reports = my_reports.filter(status__in=ACTIVE_INCIDENT_STATUSES).count()
        my_closed_reports = my_reports.filter(status=Incident.Status.CLOSED).count()
        my_pending_review_reports = my_reports.filter(status=Incident.Status.PENDING).count()
        dashboard_focus_items = [
            f"{my_pending_review_reports} report(s) are waiting for admin review.",
            f"{my_open_reports} report(s) are in progress.",
            f"{unread_notifications} unread notification(s) are available in your inbox.",
        ]

    return render(
        request,
        "dashboard/index.html",
        {
            "display_name": request.user.get_full_name() or request.user.username,
            "role_label": role_label,
            "is_admin": is_admin,
            "is_responder": is_responder,
            "is_reporter": is_reporter,
            "profile_photo_url": profile_photo_url,
            "dashboard_intro_title": dashboard_intro_title,
            "dashboard_intro_text": dashboard_intro_text,
            "dashboard_focus_items": dashboard_focus_items,
            "dashboard_role_theme": dashboard_role_theme,
            "dashboard_quick_actions": dashboard_quick_actions,
            "today_reports": today_reports,
            "active_alerts": active_alerts,
            "total_responders": total_responders,
            "resolved_count": resolved_count,
            "avg_verify_minutes": avg_verify_minutes,
            "avg_dispatch_minutes": avg_dispatch_minutes,
            "pending_incidents": pending_incidents,
            "pending_user_verifications": pending_user_verifications,
            "open_status_enquiries": open_status_enquiries,
            "my_open_reports": my_open_reports,
            "my_closed_reports": my_closed_reports,
            "my_pending_review_reports": my_pending_review_reports,
            "my_new_assignment_requests": my_new_assignment_requests,
            "my_active_assignments": my_active_assignments,
            "my_completed_tasks": my_completed_tasks,
            "my_response_points": my_response_points,
            "my_rank": my_rank,
            "top_responders": top_responders,
            "unread_notifications": unread_notifications,
            "show_responder_checkin_popup": show_responder_checkin_popup,
            "responder_checkin_reason": responder_checkin_reason,
            "responder_is_available": responder_is_available,
            "responder_latitude": responder_latitude,
            "responder_longitude": responder_longitude,
        },
    )


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return redirect("disaster_management:dashboard_home")

    form = PasswordResetOTPRequestForm(request.POST or None)
    if request.method == "POST":
        if not form.is_valid():
            messages.error(request, _form_error_message(form, fallback="Please enter a valid email address."))
            return render(request, "auth/password_reset_form.html", {"form": form}, status=400)

        email = form.cleaned_data["email"]
        request.session[PASSWORD_RESET_SESSION_EMAIL_KEY] = email
        user = _password_reset_target_user(email)
        verify_url = _password_reset_verify_url(email)
        if user is None:
            messages.error(request, "No account is linked to this email address.")
            return render(request, "auth/password_reset_form.html", {"form": form}, status=400)

        challenge = None
        try:
            challenge, otp_code, wait_seconds = _issue_password_reset_challenge(user)
        except Exception:
            messages.error(request, "We could not process your request right now. Please try again.")
            return render(request, "auth/password_reset_form.html", {"form": form}, status=500)

        if wait_seconds > 0:
            messages.warning(
                request,
                f"Please wait {wait_seconds} second(s) before requesting another code.",
            )
            return redirect(verify_url)

        try:
            _send_password_reset_otp_email(user, otp_code)
        except Exception:
            if challenge is not None and not challenge.is_used:
                challenge.is_used = True
                challenge.used_at = timezone.now()
                challenge.save(update_fields=["is_used", "used_at", "updated_at"])
            messages.error(request, "We could not send the reset code. Please try again.")
            return render(request, "auth/password_reset_form.html", {"form": form}, status=500)

        messages.success(request, "A one-time code has been sent to your email.")
        return redirect(verify_url)

    return render(request, "auth/password_reset_form.html", {"form": form})


def password_reset_verify_view(request):
    if request.user.is_authenticated:
        return redirect("disaster_management:dashboard_home")

    if request.method == "POST":
        form = PasswordResetOTPVerifyForm(request.POST)
        if not form.is_valid():
            messages.error(request, _form_error_message(form, fallback="Please enter your email and code."))
            return render(request, "auth/password_reset_verify.html", {"form": form}, status=400)

        email = form.cleaned_data["email"]
        otp_code = form.cleaned_data["otp"]
        user = _password_reset_target_user(email)
        challenge = _latest_active_password_reset_challenge(user) if user else None
        now = timezone.now()

        if not challenge or challenge.expires_at <= now:
            messages.error(request, "Code expired or not found. Request a new code.")
            return render(request, "auth/password_reset_verify.html", {"form": form}, status=400)

        if challenge.attempts_remaining <= 0:
            messages.error(request, "Too many wrong attempts. Request a new code.")
            return render(request, "auth/password_reset_verify.html", {"form": form}, status=429)

        if not check_password(otp_code, challenge.otp_hash):
            challenge.attempts_remaining = max(0, challenge.attempts_remaining - 1)
            challenge.save(update_fields=["attempts_remaining", "updated_at"])
            if challenge.attempts_remaining == 0:
                messages.error(request, "Too many wrong attempts. Request a new code.")
            else:
                messages.error(request, f"Code is not correct. {challenge.attempts_remaining} attempt(s) left.")
            return render(request, "auth/password_reset_verify.html", {"form": form}, status=400)

        challenge.is_used = True
        challenge.used_at = now
        challenge.save(update_fields=["is_used", "used_at", "updated_at"])
        PasswordResetOTP.objects.filter(user=user, is_used=False).exclude(id=challenge.id).update(
            is_used=True,
            used_at=now,
            updated_at=now,
        )
        _set_password_reset_verified_session(request, user, email)
        messages.success(request, "Code verified. Set your new password.")
        return redirect("disaster_management:password_reset_new_password")

    initial_email = (request.GET.get("email") or request.session.get(PASSWORD_RESET_SESSION_EMAIL_KEY) or "").strip().lower()
    form = PasswordResetOTPVerifyForm(initial={"email": initial_email})
    return render(request, "auth/password_reset_verify.html", {"form": form})


@require_POST
def password_reset_resend_view(request):
    if request.user.is_authenticated:
        return redirect("disaster_management:dashboard_home")

    form = PasswordResetOTPRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, fallback="Please enter a valid email address."))
        return redirect("disaster_management:password_reset")

    email = form.cleaned_data["email"]
    request.session[PASSWORD_RESET_SESSION_EMAIL_KEY] = email
    user = _password_reset_target_user(email)
    if user is None:
        messages.error(request, "No account is linked to this email address.")
        return redirect("disaster_management:password_reset")
    verify_url = _password_reset_verify_url(email)

    challenge = None
    try:
        challenge, otp_code, wait_seconds = _issue_password_reset_challenge(user)
    except Exception:
        messages.error(request, "We could not process your request right now. Please try again.")
        return redirect(verify_url)

    if wait_seconds > 0:
        messages.warning(
            request,
            f"Please wait {wait_seconds} second(s) before requesting another code.",
        )
        return redirect(verify_url)

    try:
        _send_password_reset_otp_email(user, otp_code)
    except Exception:
        if challenge is not None and not challenge.is_used:
            challenge.is_used = True
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["is_used", "used_at", "updated_at"])
        messages.error(request, "We could not send the reset code. Please try again.")
        return redirect(verify_url)

    messages.success(request, "A new one-time code has been sent to your email.")
    return redirect(verify_url)


def password_reset_new_password_view(request):
    if request.user.is_authenticated:
        return redirect("disaster_management:dashboard_home")

    user = _get_verified_password_reset_user(request)
    if user is None:
        messages.warning(request, "Verify your reset code first.")
        return redirect("disaster_management:password_reset")

    form = PasswordResetSetPasswordForm(user, request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            now = timezone.now()
            PasswordResetOTP.objects.filter(user=user, is_used=False).update(
                is_used=True,
                used_at=now,
                updated_at=now,
            )
            _clear_password_reset_session(request)
            messages.success(request, "Password updated successfully.")
            return redirect("disaster_management:password_reset_complete")

        messages.error(request, _form_error_message(form, fallback="Please check your new password and try again."))
        return render(request, "auth/password_reset_confirm.html", {"form": form}, status=400)

    return render(request, "auth/password_reset_confirm.html", {"form": form})


@require_GET
def password_reset_complete_view(request):
    return render(request, "auth/password_reset_complete.html")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("disaster_management:dashboard_home")

    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        remember_me = request.POST.get("remember_me") == "on"
        if not email or not password:
            messages.warning(request, "Email and password are required.")
            return render(request, "auth/login.html", status=400)

        user = authenticate(request, email=email, password=password)
        if user is not None:
            if hasattr(user, "profile") and user.profile.is_blocked:
                messages.error(request, "Your account is blocked by admin review.")
                return render(request, "auth/login.html", status=403)
            login(request, user)
            if remember_me:
                # Keep the session for 30 days when user opts in.
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                # Session expires when browser closes.
                request.session.set_expiry(0)
            messages.success(request, "Login successful.")
            return redirect("disaster_management:dashboard_home")

        db_user = get_user_model().objects.filter(email__iexact=email).first()
        if db_user and db_user.check_password(password):
            if hasattr(db_user, "profile") and db_user.profile.is_blocked:
                messages.error(request, "Your account is blocked by admin review.")
                return render(request, "auth/login.html", status=403)
            if not db_user.is_active:
                messages.warning(request, "Your responder account is waiting for admin approval.")
                return render(request, "auth/login.html", status=403)

        messages.error(request, "Email or password is not correct.")
        return render(request, "auth/login.html", status=401)

    return render(request, "auth/login.html")


def register_view(request):
    return register_with_role_view(request, None)


def register_with_role_view(request, role):
    if request.user.is_authenticated:
        return redirect("disaster_management:dashboard_home")

    role_choices = {choice[0] for choice in UserProfile.Role.choices}
    preset_role = role if role in role_choices else None

    if request.method == "POST":
        post_data = request.POST.copy()
        if preset_role:
            # Role-specific registration routes should not be overridable from request payload.
            post_data["role"] = preset_role
        form = UserRegistrationForm(post_data, request.FILES or None)
        if form.is_valid():
            selected_role = form.cleaned_data["role"]
            requires_admin_approval = selected_role == UserProfile.Role.RESPONDER
            user = form.save(commit=False)
            user.email = form.cleaned_data["email"]
            user.is_active = not requires_admin_approval
            user.save()
            profile = user.profile
            profile.role = selected_role
            profile.verification_status = (
                UserProfile.VerificationStatus.PENDING
                if requires_admin_approval
                else UserProfile.VerificationStatus.APPROVED
            )
            profile.verified_at = None if requires_admin_approval else timezone.now()
            profile.verified_by = None
            profile_photo = form.cleaned_data.get("profile_photo")
            if profile_photo:
                profile.profile_photo = profile_photo
                profile.save(
                    update_fields=[
                        "role",
                        "verification_status",
                        "verified_at",
                        "verified_by",
                        "profile_photo",
                        "updated_at",
                    ]
                )
            else:
                profile.save(update_fields=["role", "verification_status", "verified_at", "verified_by", "updated_at"])

            if requires_admin_approval:
                messages.warning(request, "Registration submitted. Your responder account will be reviewed by admin.")
                return redirect("disaster_management:pending_approval")

            messages.success(request, "Registration successful. You can sign in now.")
            return redirect("disaster_management:login")
        messages.error(request, _form_error_message(form))
        return render(
            request,
            "auth/register.html",
            {"form": form, "preset_role": preset_role},
            status=400,
        )

    form = UserRegistrationForm()
    if preset_role:
        form.fields["role"].initial = preset_role

    return render(request, "auth/register.html", {"form": form, "preset_role": preset_role})


@require_GET
def pending_approval_view(request):
    return render(request, "auth/pending_approval.html")


@login_required
def report_incident_page(request):
    if not (request.user.is_superuser or request.user.profile.verification_status == UserProfile.VerificationStatus.APPROVED):
        messages.warning(request, "Your account is not approved to report incidents yet.")
        return redirect("disaster_management:pending_approval")

    quota = _report_quota_snapshot(request.user)

    if request.method == "POST":
        allowed, limit_message = _enforce_reporting_limits(request.user)
        if not allowed:
            messages.error(request, limit_message)
            form = IncidentReportForm(request.POST, request.FILES or None)
            return render(
                request,
                "dashboard/incident_report.html",
                {
                    "form": form,
                    "active_menu": "incident_report",
                    "quota": quota,
                },
                status=429,
            )
        form = IncidentReportForm(request.POST, request.FILES or None)
    else:
        form = IncidentReportForm(
            initial={
                "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "affected_radius_km": 1.0,
            }
        )

    if request.method == "POST":
        if form.is_valid():
            attachments = request.FILES.getlist("attachments")
            if len(attachments) > 6:
                messages.error(request, "You can upload up to 6 attachments only.")
                return render(
                    request,
                    "dashboard/incident_report.html",
                    {"form": form, "active_menu": "incident_report", "quota": quota},
                )

            incident = form.save(commit=False)
            incident.reporter = request.user
            incident.report_mode = Incident.ReportMode.NORMAL

            _apply_triage_to_incident(incident, form.cleaned_data["description"], allow_lower_severity=True)

            duplicate_candidate = _find_duplicate_candidate(incident)
            if duplicate_candidate:
                incident.is_duplicate_suspected = True
                incident.duplicate_of = duplicate_candidate

            incident.save()

            for file_obj in attachments:
                IncidentMedia.objects.create(incident=incident, file=file_obj)

            IncidentUpdate.objects.create(
                incident=incident,
                actor=request.user,
                status=incident.status,
                note="Normal incident created by reporter.",
            )
            _log_notification(
                incident=incident,
                user=request.user,
                target_type=NotificationLog.TargetType.REPORTER,
                subject=f"Report #{incident.id} received",
                message="We received your report. Our team will review it shortly.",
            )
            _notify_admins(
                incident=incident,
                subject=f"New reported incident #{incident.id}",
                message=f"Reporter {request.user.username} submitted an incident that needs verification.",
            )
            normal_alerts_sent = _notify_nearest_responders_for_normal_incident(incident, limit=1)

            if incident.is_duplicate_suspected:
                messages.warning(request, "Incident submitted. Similar report found nearby and flagged for review.")
            else:
                messages.success(request, "Incident reported successfully.")
            if normal_alerts_sent:
                messages.info(request, "Admin and nearest responder were notified.")
            else:
                messages.info(request, "Admin notified. No available nearby responder found right now.")
            warnings, _ = _run_consequence_intelligence_and_notify(incident)
            if warnings:
                messages.warning(request, "Nearby safety warning was sent based on incident impact prediction.")
            return redirect("disaster_management:incident_detail", incident_id=incident.id)

        messages.error(request, _form_error_message(form, "Please check incident details and try again."))

    return render(
        request,
        "dashboard/incident_report.html",
        {"form": form, "active_menu": "incident_report", "quota": quota},
    )


@login_required
def emergency_incident_report_page(request):
    if not (request.user.is_superuser or request.user.profile.verification_status == UserProfile.VerificationStatus.APPROVED):
        messages.warning(request, "Your account is not approved to report incidents yet.")
        return redirect("disaster_management:pending_approval")

    quota = _report_quota_snapshot(request.user)

    if request.method == "POST":
        allowed, limit_message = _enforce_reporting_limits(request.user)
        if not allowed:
            messages.error(request, limit_message)
            form = EmergencyIncidentReportForm(request.POST, request.FILES or None)
            return render(
                request,
                "dashboard/incident_report_emergency.html",
                {
                    "form": form,
                    "active_menu": "incident_report_emergency",
                    "quota": quota,
                },
                status=429,
            )
        form = EmergencyIncidentReportForm(request.POST, request.FILES or None)
    else:
        form = EmergencyIncidentReportForm(
            initial={
                "severity": Incident.Severity.CRITICAL,
                "affected_radius_km": 3.0,
                "people_affected": 0,
            }
        )

    if request.method == "POST":
        if form.is_valid():
            attachments = request.FILES.getlist("attachments")
            if len(attachments) > 6:
                messages.error(request, "You can upload up to 6 attachments only.")
                return render(
                    request,
                    "dashboard/incident_report_emergency.html",
                    {"form": form, "active_menu": "incident_report_emergency", "quota": quota},
                )

            incident = form.save(commit=False)
            incident.reporter = request.user
            incident.report_mode = Incident.ReportMode.EMERGENCY
            incident.occurred_at = timezone.now()

            _apply_triage_to_incident(incident, form.cleaned_data["description"], allow_lower_severity=False)

            duplicate_candidate = _find_duplicate_candidate(incident)
            if duplicate_candidate:
                incident.is_duplicate_suspected = True
                incident.duplicate_of = duplicate_candidate

            incident.save()

            for file_obj in attachments:
                IncidentMedia.objects.create(incident=incident, file=file_obj)

            IncidentUpdate.objects.create(
                incident=incident,
                actor=request.user,
                status=incident.status,
                note="Emergency incident created by reporter.",
            )
            _log_notification(
                incident=incident,
                user=request.user,
                target_type=NotificationLog.TargetType.REPORTER,
                subject=f"Emergency report #{incident.id} received",
                message="We received your emergency report. Response teams are being alerted now.",
            )
            _notify_admins(
                incident=incident,
                subject=f"Emergency alert #{incident.id}",
                message=f"Emergency incident was posted by {request.user.username}.",
            )

            incident.status = Incident.Status.VERIFIED
            incident.verified_at = timezone.now()
            incident.save(update_fields=["status", "verified_at", "updated_at"])
            IncidentUpdate.objects.create(
                incident=incident,
                actor=request.user,
                status=incident.status,
                note="Emergency report submitted with fast-track verification.",
            )
            assignments_created, fallback_used = _dispatch_emergency_with_fallback(
                incident,
                nearby_radius_km=10.0,
                limit=8,
            )
            if assignments_created:
                incident.status = Incident.Status.DISPATCHED
                incident.dispatched_at = timezone.now()
                incident.save(update_fields=["status", "dispatched_at", "updated_at"])
                IncidentUpdate.objects.create(
                    incident=incident,
                    actor=request.user,
                    status=incident.status,
                    note=f"Emergency dispatch sent to {assignments_created} responders.",
                )
                if fallback_used:
                    messages.warning(
                        request,
                        "Emergency alert posted. No nearby responder found, so the closest available responder was alerted automatically.",
                    )
                else:
                    messages.warning(request, "Emergency alert posted and nearby responders were notified immediately.")
            else:
                messages.warning(request, "Emergency alert posted. No nearby responder available right now.")
            if incident.is_duplicate_suspected:
                messages.warning(request, "Similar incident found nearby. It was marked for admin duplicate review.")
            warnings, _ = _run_consequence_intelligence_and_notify(incident)
            if warnings:
                messages.warning(request, "Nearby safety warning was sent based on emergency impact prediction.")
            return redirect("disaster_management:incident_detail", incident_id=incident.id)

        messages.error(request, _form_error_message(form, "Please check incident details and try again."))

    return render(
        request,
        "dashboard/incident_report_emergency.html",
        {
            "form": form,
            "active_menu": "incident_report_emergency",
            "quota": quota,
        },
    )


@login_required
def incident_list_view(request):
    role = request.user.profile.role
    is_responder = role == UserProfile.Role.RESPONDER
    is_reporter = role == UserProfile.Role.REPORTER

    visibility = (request.GET.get("visibility") or "").strip()
    if request.user.is_superuser:
        if visibility not in {"all", "mine"}:
            visibility = "all"
    elif is_responder:
        visibility = "assigned"
    else:
        visibility = "mine"

    base_query = Incident.objects.select_related("reporter")
    if visibility == "mine":
        base_query = base_query.filter(reporter=request.user)
    elif visibility == "assigned":
        base_query = base_query.filter(assignments__responder=request.user).distinct()

    if is_responder:
        ready, reason = _responder_ready(request.user)
        if not ready:
            messages.warning(request, reason)
            return redirect("disaster_management:profile_edit")

    summary_query = base_query
    summary_counts = {
        "total": summary_query.count(),
        "active": summary_query.filter(status__in=ACTIVE_INCIDENT_STATUSES).count(),
        "history": summary_query.filter(status__in=HISTORY_INCIDENT_STATUSES).count(),
        "pending": summary_query.filter(status=Incident.Status.PENDING).count(),
        "dispatched": summary_query.filter(status=Incident.Status.DISPATCHED).count(),
        "closed": summary_query.filter(status=Incident.Status.CLOSED).count(),
    }

    scope = (request.GET.get("scope") or "").strip().lower()
    if scope not in {"active", "history", "all"}:
        scope = "active"

    query = summary_query
    if scope == "active":
        query = query.filter(status__in=ACTIVE_INCIDENT_STATUSES)
    elif scope == "history":
        query = query.filter(status__in=HISTORY_INCIDENT_STATUSES)

    status = request.GET.get("status")
    incident_type = request.GET.get("incident_type")
    report_mode = request.GET.get("report_mode")
    keyword = request.GET.get("q", "").strip()
    from_date_raw = request.GET.get("from_date", "").strip()
    to_date_raw = request.GET.get("to_date", "").strip()

    if status in dict(Incident.Status.choices):
        query = query.filter(status=status)
    if incident_type in dict(Incident.IncidentType.choices):
        query = query.filter(incident_type=incident_type)
    if report_mode in dict(Incident.ReportMode.choices):
        query = query.filter(report_mode=report_mode)
    if keyword:
        query = query.filter(Q(title__icontains=keyword) | Q(description__icontains=keyword) | Q(address__icontains=keyword))

    from_date = _parse_date_or_none(from_date_raw)
    to_date = _parse_date_or_none(to_date_raw)
    if from_date_raw and from_date is None:
        messages.warning(request, "From date was ignored because format is invalid. Use YYYY-MM-DD.")
    if to_date_raw and to_date is None:
        messages.warning(request, "To date was ignored because format is invalid. Use YYYY-MM-DD.")
    if from_date:
        query = query.filter(created_at__date__gte=from_date)
    if to_date:
        query = query.filter(created_at__date__lte=to_date)

    query = query.order_by("-created_at")
    paginator = Paginator(query, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    incidents = page_obj.object_list

    committed_assignment = None
    committed_incident_id = None
    committed_incident_url = ""
    if is_responder:
        committed_assignment = _active_committed_assignment(request.user)
        if committed_assignment:
            committed_incident_id = committed_assignment.incident_id
            committed_incident_url = reverse("disaster_management:incident_detail", args=[committed_incident_id])

    assignment_status_map = {}
    assignment_value_map = {}
    if is_responder and incidents:
        rows = IncidentAssignment.objects.filter(incident__in=incidents, responder=request.user).select_related("incident")
        assignment_status_map = {row.incident_id: row.get_status_display() for row in rows}
        assignment_value_map = {row.incident_id: row.status for row in rows}
    for incident in incidents:
        incident.my_assignment_status = assignment_status_map.get(incident.id, "")
        incident.my_assignment_value = assignment_value_map.get(incident.id, "")
        if is_responder:
            incident.can_open = bool(
                incident.my_assignment_value
                and incident.my_assignment_value != IncidentAssignment.AssignmentStatus.CANCELLED
                and (
                    committed_incident_id is None
                    or incident.id == committed_incident_id
                )
            )
        else:
            incident.can_open = True

    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    pagination_query = query_params.urlencode()

    return render(
        request,
        "dashboard/incident_list.html",
        {
            "incidents": incidents,
            "page_obj": page_obj,
            "summary_counts": summary_counts,
            "status_choices": Incident.Status.choices,
            "type_choices": Incident.IncidentType.choices,
            "mode_choices": Incident.ReportMode.choices,
            "selected_status": status or "",
            "selected_type": incident_type or "",
            "selected_mode": report_mode or "",
            "selected_scope": scope,
            "selected_visibility": visibility,
            "keyword": keyword,
            "from_date": from_date_raw,
            "to_date": to_date_raw,
            "is_responder": is_responder,
            "is_reporter": is_reporter,
            "pagination_query": pagination_query,
            "committed_assignment": committed_assignment,
            "committed_incident_url": committed_incident_url,
            "committed_incident_id": committed_incident_id,
        },
    )


@login_required
def incident_history_view(request):
    role = request.user.profile.role
    is_responder = role == UserProfile.Role.RESPONDER
    is_reporter = role == UserProfile.Role.REPORTER

    visibility = (request.GET.get("visibility") or "").strip()
    if request.user.is_superuser:
        if visibility not in {"all", "mine"}:
            visibility = "all"
    elif is_responder:
        visibility = "assigned"
    else:
        visibility = "mine"

    base_query = Incident.objects.select_related("reporter").filter(status__in=HISTORY_INCIDENT_STATUSES)
    if visibility == "mine":
        base_query = base_query.filter(reporter=request.user)
    elif visibility == "assigned":
        base_query = base_query.filter(assignments__responder=request.user).distinct()

    status = request.GET.get("status")
    incident_type = request.GET.get("incident_type")
    report_mode = request.GET.get("report_mode")
    keyword = request.GET.get("q", "").strip()
    from_date_raw = request.GET.get("from_date", "").strip()
    to_date_raw = request.GET.get("to_date", "").strip()

    query = base_query
    if status in dict(Incident.Status.choices):
        query = query.filter(status=status)
    if incident_type in dict(Incident.IncidentType.choices):
        query = query.filter(incident_type=incident_type)
    if report_mode in dict(Incident.ReportMode.choices):
        query = query.filter(report_mode=report_mode)
    if keyword:
        query = query.filter(Q(title__icontains=keyword) | Q(description__icontains=keyword) | Q(address__icontains=keyword))

    from_date = _parse_date_or_none(from_date_raw)
    to_date = _parse_date_or_none(to_date_raw)
    if from_date_raw and from_date is None:
        messages.warning(request, "From date was ignored because format is invalid. Use YYYY-MM-DD.")
    if to_date_raw and to_date is None:
        messages.warning(request, "To date was ignored because format is invalid. Use YYYY-MM-DD.")
    if from_date:
        query = query.filter(created_at__date__gte=from_date)
    if to_date:
        query = query.filter(created_at__date__lte=to_date)

    query = query.order_by("-closed_at", "-updated_at", "-created_at")
    paginator = Paginator(query, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    incidents = page_obj.object_list

    assignment_status_map = {}
    if is_responder and incidents:
        rows = IncidentAssignment.objects.filter(incident__in=incidents, responder=request.user).select_related("incident")
        assignment_status_map = {row.incident_id: row.get_status_display() for row in rows}
    for incident in incidents:
        incident.my_assignment_status = assignment_status_map.get(incident.id, "")

    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    pagination_query = query_params.urlencode()

    summary_counts = {
        "total": base_query.count(),
        "closed": base_query.filter(status=Incident.Status.CLOSED).count(),
        "rejected": base_query.filter(status=Incident.Status.REJECTED).count(),
        "emergency": base_query.filter(report_mode=Incident.ReportMode.EMERGENCY).count(),
        "normal": base_query.filter(report_mode=Incident.ReportMode.NORMAL).count(),
    }

    return render(
        request,
        "dashboard/incident_history.html",
        {
            "incidents": incidents,
            "page_obj": page_obj,
            "summary_counts": summary_counts,
            "status_choices": Incident.Status.choices,
            "type_choices": Incident.IncidentType.choices,
            "mode_choices": Incident.ReportMode.choices,
            "selected_status": status or "",
            "selected_type": incident_type or "",
            "selected_mode": report_mode or "",
            "selected_visibility": visibility,
            "keyword": keyword,
            "from_date": from_date_raw,
            "to_date": to_date_raw,
            "is_responder": is_responder,
            "is_reporter": is_reporter,
            "pagination_query": pagination_query,
        },
    )


@login_required
def incident_detail_view(request, incident_id):
    incident = get_object_or_404(Incident.objects.select_related("reporter"), id=incident_id)
    role = request.user.profile.role
    my_assignment = None
    active_commitment = None
    if role == UserProfile.Role.RESPONDER:
        my_assignment = incident.assignments.filter(responder=request.user).first()
        active_commitment = _active_committed_assignment(request.user)

    if not request.user.is_superuser:
        if role == UserProfile.Role.RESPONDER:
            if my_assignment is None:
                messages.error(request, "Responder can open only assigned incidents.")
                return redirect("disaster_management:incident_list")
            if active_commitment and active_commitment.incident_id != incident.id:
                messages.warning(
                    request,
                    f"You are committed to incident #{active_commitment.incident_id}. Open that incident to continue response workflow.",
                )
                return redirect("disaster_management:incident_detail", incident_id=active_commitment.incident_id)
        elif incident.reporter_id != request.user.id:
            messages.error(request, "You are not allowed to view this incident.")
            return redirect("disaster_management:incident_list")

    update_form = IncidentStatusUpdateForm(initial={"status": incident.status})
    status_steps, allowed_next_statuses = _responder_status_steps(incident)
    on_site_ready = bool(
        incident.first_response_at
        or incident.status in {Incident.Status.ON_SCENE, Incident.Status.STABILIZED, Incident.Status.ESCALATED, Incident.Status.CLOSED}
    )
    accepted_assignment = _accepted_assignment(request.user, incident)
    is_stale_for_enquiry, stale_days_without_update, last_update_at = _is_incident_stale_for_enquiry(incident)
    open_status_enquiry = _open_status_enquiry(incident)
    latest_status_enquiry = _latest_status_enquiry(incident)
    status_enquiry_history = incident.status_enquiries.select_related(
        "requested_by",
        "responded_by",
        "finalized_by",
        "overdue_action_by",
    ).order_by("-requested_at", "-created_at")[:8]

    status_update_requires_admin_enquiry = bool(
        request.user.profile.role == UserProfile.Role.RESPONDER
        and accepted_assignment
        and is_stale_for_enquiry
        and open_status_enquiry is None
        and incident.status not in {Incident.Status.CLOSED, Incident.Status.REJECTED}
    )
    can_update = bool(accepted_assignment and not status_update_requires_admin_enquiry and open_status_enquiry is None)
    show_responder_status_update = incident.status != Incident.Status.STABILIZED
    responder_ready = True
    responder_ready_reason = ""
    commitment_block_reason = ""
    stale_update_block_reason = ""
    if status_update_requires_admin_enquiry:
        stale_update_block_reason = (
            f"No incident update for {stale_days_without_update} day(s). "
            "Wait for admin status enquiry request before posting the next status update."
        )
    if request.user.profile.role == UserProfile.Role.RESPONDER:
        responder_ready, responder_ready_reason = _responder_ready(request.user)
        if active_commitment and active_commitment.incident_id != incident.id:
            commitment_block_reason = (
                f"You are already committed to incident #{active_commitment.incident_id}. "
                "Finish that workflow before accepting another incident."
            )
    can_accept_assignment = bool(
        my_assignment
        and my_assignment.status == IncidentAssignment.AssignmentStatus.NOTIFIED
        and responder_ready
        and not commitment_block_reason
        and incident.status not in {Incident.Status.REJECTED, Incident.Status.CLOSED}
    )
    final_review = getattr(incident, "final_review", None)
    final_review_stage_ready = incident.status in {Incident.Status.STABILIZED, Incident.Status.ESCALATED}
    can_submit_final_review = bool(
        final_review_stage_ready
        and accepted_assignment
        and (final_review is None or final_review.responder_id == request.user.id)
        and incident.status != Incident.Status.CLOSED
    )
    final_review_ready_reason = ""
    if (
        request.user.profile.role == UserProfile.Role.RESPONDER
        and accepted_assignment
        and incident.status != Incident.Status.CLOSED
        and (final_review is None or final_review.responder_id == request.user.id)
        and not can_submit_final_review
    ):
        final_review_ready_reason = "Set incident status to Stabilized or Escalated before submitting final review."
    final_review_form = None
    if can_submit_final_review:
        final_review_form = IncidentFinalReviewForm(instance=final_review)
    my_fake_report = None
    can_submit_fake_report = False
    fake_report_form = None
    if request.user.profile.role == UserProfile.Role.RESPONDER:
        my_fake_report = incident.fake_reports.filter(responder=request.user).first()
        can_submit_fake_report = bool(
            incident.status == Incident.Status.ON_SCENE
            and accepted_assignment
            and my_fake_report is None
            and incident.status != Incident.Status.CLOSED
        )
        if can_submit_fake_report:
            fake_report_form = IncidentFakeReportForm()

    enquiry_is_overdue = _is_enquiry_overdue(open_status_enquiry)
    can_admin_request_status_enquiry = bool(
        request.user.is_superuser
        and incident.status not in {Incident.Status.CLOSED, Incident.Status.REJECTED}
        and is_stale_for_enquiry
        and open_status_enquiry is None
    )
    can_responder_answer_status_enquiry = bool(
        request.user.profile.role == UserProfile.Role.RESPONDER
        and accepted_assignment
        and open_status_enquiry is not None
        and open_status_enquiry.status == IncidentStatusEnquiry.WorkflowStatus.REQUESTED
    )
    can_admin_finalize_status_enquiry = bool(
        request.user.is_superuser
        and latest_status_enquiry is not None
        and latest_status_enquiry.status == IncidentStatusEnquiry.WorkflowStatus.RESPONDED
    )
    can_admin_take_overdue_action = bool(
        request.user.is_superuser
        and open_status_enquiry is not None
        and enquiry_is_overdue
        and open_status_enquiry.overdue_action == IncidentStatusEnquiry.OverdueAction.NONE
    )
    status_enquiry_request_form = IncidentStatusEnquiryRequestForm()
    status_enquiry_response_form = IncidentStatusEnquiryResponseForm(
        initial={
            "current_status": incident.status if incident.status in {
                Incident.Status.DISPATCHED,
                Incident.Status.ON_SCENE,
                Incident.Status.STABILIZED,
                Incident.Status.ESCALATED,
            } else Incident.Status.DISPATCHED
        }
    )
    status_enquiry_finalize_form = IncidentStatusEnquiryFinalizeForm()
    status_enquiry_overdue_action_form = IncidentStatusEnquiryOverdueActionForm()
    closure_ready, closure_requirements, closure_missing_requirements = _closure_requirements(incident, final_review)

    workflow_steps = []
    for step in status_steps:
        value = step["value"]
        is_current = incident.status == value
        is_available = value in allowed_next_statuses
        if value == Incident.Status.ON_SCENE:
            is_completed = incident.status in {
                Incident.Status.ON_SCENE,
                Incident.Status.STABILIZED,
                Incident.Status.ESCALATED,
                Incident.Status.CLOSED,
            }
        elif value == Incident.Status.STABILIZED:
            is_completed = incident.status in {Incident.Status.STABILIZED, Incident.Status.CLOSED}
        else:
            is_completed = incident.status == Incident.Status.ESCALATED
        workflow_steps.append(
            {
                "value": value,
                "label": step["label"],
                "is_current": is_current,
                "is_available": is_available,
                "is_completed": is_completed,
            }
        )
    available_workflow_steps = [step for step in workflow_steps if step["is_available"]]

    navigate_url = f"https://www.google.com/maps/dir/?api=1&destination={incident.latitude},{incident.longitude}"
    if request.user.profile.role == UserProfile.Role.RESPONDER:
        responder = getattr(request.user, "responder_profile", None)
        if responder and responder.latitude is not None and responder.longitude is not None:
            navigate_url = (
                "https://www.google.com/maps/dir/?api=1"
                f"&origin={responder.latitude},{responder.longitude}"
                f"&destination={incident.latitude},{incident.longitude}"
            )
    consequence_items = [line.strip() for line in (incident.ai_consequence_summary or "").splitlines() if line.strip()]
    return render(
        request,
        "dashboard/incident_detail.html",
        {
            "incident": incident,
            "updates": incident.updates.select_related("actor").order_by("-created_at"),
            "media_items": incident.media.all(),
            "can_update": can_update,
            "show_responder_status_update": show_responder_status_update,
            "update_form": update_form,
            "assignments": incident.assignments.select_related("responder").order_by("distance_km"),
            "my_assignment": my_assignment,
            "can_accept_assignment": can_accept_assignment,
            "responder_ready_reason": responder_ready_reason,
            "commitment_block_reason": commitment_block_reason,
            "stale_update_block_reason": stale_update_block_reason,
            "navigate_url": navigate_url,
            "final_review": final_review,
            "final_review_media": final_review.media.all() if final_review else [],
            "can_submit_final_review": can_submit_final_review,
            "final_review_form": final_review_form,
            "final_review_ready_reason": final_review_ready_reason,
            "closure_ready": closure_ready,
            "closure_requirements": closure_requirements,
            "closure_missing_requirements": closure_missing_requirements,
            "fake_reports": incident.fake_reports.select_related("responder", "reviewed_by").prefetch_related("media").order_by("-created_at"),
            "can_submit_fake_report": can_submit_fake_report,
            "fake_report_form": fake_report_form,
            "my_fake_report": my_fake_report,
            "consequence_items": consequence_items,
            "risk_predictions": incident.risk_predictions.all(),
            "status_steps": status_steps,
            "workflow_steps": workflow_steps,
            "available_workflow_steps": available_workflow_steps,
            "allowed_next_statuses": list(allowed_next_statuses),
            "on_site_ready": on_site_ready,
            "is_stale_for_enquiry": is_stale_for_enquiry,
            "stale_days_without_update": stale_days_without_update,
            "last_update_at": last_update_at,
            "open_status_enquiry": open_status_enquiry,
            "latest_status_enquiry": latest_status_enquiry,
            "status_enquiry_history": status_enquiry_history,
            "can_admin_request_status_enquiry": can_admin_request_status_enquiry,
            "can_responder_answer_status_enquiry": can_responder_answer_status_enquiry,
            "can_admin_finalize_status_enquiry": can_admin_finalize_status_enquiry,
            "can_admin_take_overdue_action": can_admin_take_overdue_action,
            "enquiry_is_overdue": enquiry_is_overdue,
            "status_enquiry_request_form": status_enquiry_request_form,
            "status_enquiry_response_form": status_enquiry_response_form,
            "status_enquiry_finalize_form": status_enquiry_finalize_form,
            "status_enquiry_overdue_action_form": status_enquiry_overdue_action_form,
            "stale_status_enquiry_days": STALE_STATUS_ENQUIRY_DAYS,
            "enquiry_response_overdue_hours": ENQUIRY_RESPONSE_OVERDUE_HOURS,
            "on_scene_max_distance_km": ON_SCENE_MAX_DISTANCE_KM,
        },
    )


@login_required
def incident_map_view(request):
    if not (request.user.is_superuser or request.user.profile.role == UserProfile.Role.RESPONDER):
        messages.info(request, "Incident map is available for admin and responder users only.")
        return redirect("disaster_management:incident_list")
    ready, reason = _responder_ready(request.user)
    if request.user.profile.role == UserProfile.Role.RESPONDER and not ready:
        messages.warning(request, reason)
        return redirect("disaster_management:profile_edit")
    return render(request, "dashboard/incident_map.html")


@login_required
@require_GET
def incident_map_data_view(request):
    if not (request.user.is_superuser or request.user.profile.role == UserProfile.Role.RESPONDER):
        return JsonResponse({"message": "Incident map is available for admin and responder users only."}, status=403)
    query = Incident.objects.exclude(status__in=[Incident.Status.REJECTED, Incident.Status.CLOSED]).order_by("-created_at")
    navigate_origin = None
    if request.user.profile.role == UserProfile.Role.RESPONDER:
        responder = getattr(request.user, "responder_profile", None)
        if responder and responder.latitude is not None and responder.longitude is not None:
            navigate_origin = f"{responder.latitude},{responder.longitude}"
    payload = []
    for incident in query[:500]:
        navigate_url = "https://www.google.com/maps/dir/?api=1"
        if navigate_origin:
            navigate_url += f"&origin={navigate_origin}"
        navigate_url += f"&destination={incident.latitude},{incident.longitude}"
        payload.append(
            {
                "id": incident.id,
                "title": incident.title,
                "report_mode": incident.report_mode,
                "type": incident.get_incident_type_display(),
                "severity": incident.get_severity_display(),
                "status": incident.get_status_display(),
                "lat": float(incident.latitude),
                "lng": float(incident.longitude),
                "radius_km": incident.affected_radius_km,
                "detail_url": reverse("disaster_management:incident_detail", args=[incident.id]),
                "navigate_url": navigate_url,
            }
        )
    return JsonResponse({"incidents": payload})


@login_required
def notification_list_view(request):
    if not (request.user.is_superuser or request.user.profile.role == UserProfile.Role.RESPONDER):
        messages.info(request, "Dedicated notification page is available for admin and responder users only.")
        return redirect("disaster_management:dashboard_home")
    logs_qs = NotificationLog.objects.filter(user=request.user).select_related("incident").order_by("-created_at")
    logs_qs.filter(is_read=False).update(is_read=True, read_at=timezone.now())
    return render(request, "dashboard/notifications.html", {"logs": logs_qs[:200]})


@login_required
@require_GET
def notification_feed_view(request):
    try:
        since_id = int(request.GET.get("since_id", "0"))
    except ValueError:
        since_id = 0
    base_qs = NotificationLog.objects.filter(user=request.user).order_by("-id")
    unread_count = base_qs.filter(is_read=False).count()
    latest_id = base_qs.values_list("id", flat=True).first() or 0
    # Return the newest notifications first, then re-order ascending for stable UI rendering.
    new_qs = base_qs.filter(id__gt=since_id).select_related("incident").order_by("-id")[:20]
    new_rows = list(reversed(list(new_qs)))
    items = [
        {
            "id": row.id,
            "subject": row.subject,
            "message": row.message,
            "incident_id": row.incident_id,
            "incident_title": row.incident.title,
            "report_mode": row.incident.report_mode,
            "is_emergency": row.incident.report_mode == Incident.ReportMode.EMERGENCY,
            "is_emergency_dispatch": (
                row.incident.report_mode == Incident.ReportMode.EMERGENCY
                and row.target_type == NotificationLog.TargetType.RESPONDER
                and row.subject.startswith("New incident #")
            ),
            "is_new_emergency_alert": (
                row.incident.report_mode == Incident.ReportMode.EMERGENCY
                and (
                    (
                        row.target_type == NotificationLog.TargetType.ADMIN
                        and row.subject.startswith("Emergency alert #")
                    )
                    or (
                        row.target_type == NotificationLog.TargetType.RESPONDER
                        and row.subject.startswith("New incident #")
                    )
                )
            ),
            "target_type": row.target_type,
            "created_at": timezone.localtime(row.created_at).strftime("%Y-%m-%d %H:%M"),
            "is_read": row.is_read,
            "detail_url": reverse("disaster_management:incident_detail", args=[row.incident_id]),
            "mark_read_url": reverse("disaster_management:notification_mark_read", args=[row.id]),
        }
        for row in new_rows
    ]
    return JsonResponse({"unread_count": unread_count, "latest_id": latest_id, "items": items})


@login_required
@require_POST
def notification_mark_all_read_view(request):
    NotificationLog.objects.filter(user=request.user, is_read=False).update(is_read=True, read_at=timezone.now())
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"message": "Notifications marked as read."}, status=200)
    return redirect("disaster_management:notification_list")


@login_required
@require_POST
def notification_mark_read_view(request, notification_id):
    row = get_object_or_404(NotificationLog, id=notification_id, user=request.user)
    if not row.is_read:
        row.is_read = True
        row.read_at = timezone.now()
        row.save(update_fields=["is_read", "read_at", "updated_at"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"message": "Notification marked as read."}, status=200)
    return redirect("disaster_management:notification_list")


@login_required
@require_POST
def incident_status_enquiry_request_view(request, incident_id):
    if not request.user.is_superuser:
        messages.error(request, "Only admin can request stale incident status enquiry.")
        return redirect("disaster_management:incident_detail", incident_id=incident_id)
    incident = get_object_or_404(Incident, id=incident_id)
    if incident.status in {Incident.Status.CLOSED, Incident.Status.REJECTED}:
        messages.warning(request, "Status enquiry is not available for closed/rejected incidents.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    is_stale, stale_days, _ = _is_incident_stale_for_enquiry(incident)
    if not is_stale:
        messages.warning(
            request,
            f"Status enquiry can be requested only when there is no update for at least {STALE_STATUS_ENQUIRY_DAYS} day(s).",
        )
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if _open_status_enquiry(incident):
        messages.info(request, "A status enquiry is already pending responder response.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentStatusEnquiryRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please review status enquiry request and try again."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    request_note = (form.cleaned_data.get("request_note") or "").strip()

    target_responder = _primary_responder_for_enquiry(incident)
    enquiry = IncidentStatusEnquiry.objects.create(
        incident=incident,
        requested_by=request.user,
        target_responder=target_responder,
        request_note=request_note,
        requested_at=timezone.now(),
        status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
    )
    enquiry_note = f"Admin requested stale status update after {stale_days} day(s) without incident updates."
    if request_note:
        enquiry_note += f" Note: {request_note}"
    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note=enquiry_note,
    )

    notified_count = _notify_responders_for_status_enquiry(incident, request_note)
    if target_responder and notified_count == 0:
        _log_notification(
            incident=incident,
            user=target_responder,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=f"Status enquiry requested for incident #{incident.id}",
            message="Admin requested your current status update. Open incident details and respond in enquiry workflow.",
        )
        notified_count = 1

    _notify_admins(
        incident=incident,
        subject=f"Status enquiry opened for incident #{incident.id}",
        message=f"Admin {request.user.username} opened a stale-incident enquiry (id {enquiry.id}).",
    )

    if notified_count:
        messages.success(request, f"Status enquiry requested. {notified_count} responder notification(s) sent.")
    else:
        messages.warning(request, "Status enquiry requested, but no responder assignment was found to notify.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_status_enquiry_response_view(request, incident_id):
    incident = get_object_or_404(Incident, id=incident_id)
    if request.user.profile.role != UserProfile.Role.RESPONDER:
        messages.error(request, "Only responder can submit enquiry status response.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    ready, reason = _responder_ready(request.user)
    if not ready:
        messages.warning(request, reason)
        return redirect("disaster_management:profile_edit")
    if not _responder_allowed_for_incident(request.user, incident):
        messages.error(request, "Only accepted responder can respond to enquiry.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    enquiry = _open_status_enquiry(incident)
    if enquiry is None:
        messages.warning(request, "No pending admin enquiry found for this incident.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if enquiry.target_responder_id and enquiry.target_responder_id != request.user.id:
        messages.error(request, "This enquiry is assigned to another responder.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentStatusEnquiryResponseForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please update enquiry response and try again."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    current_status = form.cleaned_data["current_status"]
    responder_note = form.cleaned_data["responder_note"]
    recommend_finalize = bool(form.cleaned_data.get("recommend_finalize"))
    if current_status == Incident.Status.ON_SCENE:
        location_ok, location_error, _, lat_value, lon_value = _validate_on_scene_location(
            request.user,
            incident,
            require_live_location=False,
        )
        if not location_ok:
            messages.error(request, location_error)
            return redirect("disaster_management:incident_detail", incident_id=incident.id)
        responder_profile = getattr(request.user, "responder_profile", None)
        if responder_profile:
            responder_profile.latitude = lat_value
            responder_profile.longitude = lon_value
            responder_profile.last_checkin_at = timezone.now()
            responder_profile.save(update_fields=["latitude", "longitude", "last_checkin_at", "updated_at"])

    with transaction.atomic():
        enquiry = get_object_or_404(
            IncidentStatusEnquiry.objects.select_for_update(),
            id=enquiry.id,
            status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
        )
        incident_update_fields = ["updated_at"]
        status_changed = incident.status != current_status
        if status_changed:
            incident.status = current_status
            incident_update_fields.append("status")
        if current_status == Incident.Status.ON_SCENE and not incident.first_response_at:
            incident.first_response_at = timezone.now()
            incident_update_fields.append("first_response_at")
        incident.save(update_fields=incident_update_fields)

        enquiry.status = IncidentStatusEnquiry.WorkflowStatus.RESPONDED
        enquiry.responded_by = request.user
        enquiry.responder_current_status = current_status
        enquiry.responder_note = responder_note
        enquiry.responder_recommend_finalize = recommend_finalize
        enquiry.responded_at = timezone.now()
        enquiry.save(
            update_fields=[
                "status",
                "responded_by",
                "responder_current_status",
                "responder_note",
                "responder_recommend_finalize",
                "responded_at",
                "updated_at",
            ]
        )

    response_note = (
        f"Responder enquiry response submitted. Current status: {incident.get_status_display()}. "
        f"Recommend finalize: {'Yes' if recommend_finalize else 'No'}. "
        f"Note: {responder_note}"
    )
    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note=response_note,
    )
    _notify_admins(
        incident=incident,
        subject=f"Responder replied to status enquiry for incident #{incident.id}",
        message=(
            f"{request.user.username} responded with status {incident.get_status_display()}. "
            f"Recommend finalize: {'Yes' if recommend_finalize else 'No'}."
        ),
    )
    if incident.reporter:
        _log_notification(
            incident=incident,
            user=incident.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject=f"Update on your report #{incident.id}",
            message=f"Response team shared a new update: {_reporter_status_label(incident.status)}.",
        )
    messages.success(request, "Status enquiry response submitted to admin.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_status_enquiry_finalize_view(request, incident_id):
    if not request.user.is_superuser:
        messages.error(request, "Only admin can finalize enquiry.")
        return redirect("disaster_management:incident_detail", incident_id=incident_id)
    incident = get_object_or_404(Incident, id=incident_id)
    latest = _latest_status_enquiry(incident)
    if latest is None or latest.status != IncidentStatusEnquiry.WorkflowStatus.RESPONDED:
        messages.warning(request, "No responder enquiry response is pending finalization.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentStatusEnquiryFinalizeForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please review enquiry finalization form."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    admin_note = (form.cleaned_data.get("admin_note") or "").strip()
    close_incident = bool(form.cleaned_data.get("close_incident"))

    with transaction.atomic():
        enquiry = get_object_or_404(
            IncidentStatusEnquiry.objects.select_for_update(),
            id=latest.id,
            status=IncidentStatusEnquiry.WorkflowStatus.RESPONDED,
        )
        enquiry.status = IncidentStatusEnquiry.WorkflowStatus.FINALIZED
        enquiry.finalized_by = request.user
        enquiry.final_admin_note = admin_note
        enquiry.finalized_at = timezone.now()
        enquiry.save(
            update_fields=[
                "status",
                "finalized_by",
                "final_admin_note",
                "finalized_at",
                "updated_at",
            ]
        )

    finalize_note = "Admin finalized status enquiry workflow."
    if admin_note:
        finalize_note += f" Note: {admin_note}"
    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note=finalize_note,
    )
    if enquiry.responded_by:
        _log_notification(
            incident=incident,
            user=enquiry.responded_by,
            target_type=NotificationLog.TargetType.RESPONDER,
            subject=f"Status enquiry finalized for incident #{incident.id}",
            message="Admin reviewed your enquiry response and finalized the enquiry workflow.",
        )

    if close_incident:
        messages.success(request, "Status enquiry finalized. You can now use the Close Task action.")
    else:
        messages.success(request, "Status enquiry finalized.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_status_enquiry_overdue_action_view(request, incident_id):
    if not request.user.is_superuser:
        messages.error(request, "Only admin can apply overdue enquiry actions.")
        return redirect("disaster_management:incident_detail", incident_id=incident_id)
    incident = get_object_or_404(Incident, id=incident_id)
    enquiry = _open_status_enquiry(incident)
    if enquiry is None:
        messages.warning(request, "No pending enquiry available for overdue action.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if not _is_enquiry_overdue(enquiry):
        messages.info(request, "Responder response window is still active. Overdue action is available after 24 hours.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if enquiry.overdue_action != IncidentStatusEnquiry.OverdueAction.NONE:
        messages.info(request, "Overdue action was already applied for this enquiry.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentStatusEnquiryOverdueActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please select a valid overdue action."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    action_value = form.cleaned_data["action"]
    admin_note = (form.cleaned_data.get("admin_note") or "").strip()
    target_user = enquiry.target_responder or _primary_responder_for_enquiry(incident)
    if target_user is None:
        messages.warning(request, "No responder assignment available for overdue action.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    with transaction.atomic():
        enquiry = get_object_or_404(
            IncidentStatusEnquiry.objects.select_for_update().select_related("target_responder"),
            id=enquiry.id,
            status=IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
        )
        if action_value == "warning":
            target_user.profile.warning_count += 1
            target_user.profile.save(update_fields=["warning_count", "updated_at"])
            enquiry.overdue_action = IncidentStatusEnquiry.OverdueAction.WARNING
            action_message = "Warning issued to responder for delayed enquiry response."
            _log_notification(
                incident=incident,
                user=target_user,
                target_type=NotificationLog.TargetType.RESPONDER,
                subject=f"Warning: delayed response for incident #{incident.id}",
                message=(
                    "Admin issued a warning because status enquiry response was delayed over 24 hours. "
                    + (f"Note: {admin_note}" if admin_note else "")
                ).strip(),
            )
        else:
            target_user.is_active = False
            target_user.save(update_fields=["is_active"])
            target_user.profile.is_blocked = True
            target_user.profile.blocked_at = timezone.now()
            target_user.profile.blocked_reason = admin_note or "Suspended due to no response to admin status enquiry in 24 hours."
            target_user.profile.save(update_fields=["is_blocked", "blocked_at", "blocked_reason", "updated_at"])
            responder_profile = getattr(target_user, "responder_profile", None)
            if responder_profile:
                responder_profile.is_available = False
                responder_profile.save(update_fields=["is_available", "updated_at"])
            enquiry.overdue_action = IncidentStatusEnquiry.OverdueAction.SUSPEND
            action_message = "Responder account suspended for delayed enquiry response."
            _log_notification(
                incident=incident,
                user=target_user,
                target_type=NotificationLog.TargetType.RESPONDER,
                subject=f"Suspended: no enquiry response for incident #{incident.id}",
                message=(
                    "Your responder account has been suspended because enquiry response was not submitted within 24 hours. "
                    + (f"Note: {admin_note}" if admin_note else "")
                ).strip(),
            )

        enquiry.overdue_action_note = admin_note
        enquiry.overdue_action_by = request.user
        enquiry.overdue_action_at = timezone.now()
        enquiry.save(
            update_fields=[
                "overdue_action",
                "overdue_action_note",
                "overdue_action_by",
                "overdue_action_at",
                "updated_at",
            ]
        )

    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note=action_message + (f" Note: {admin_note}" if admin_note else ""),
    )
    messages.warning(request, action_message)
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_status_update_view(request, incident_id):
    incident = get_object_or_404(Incident, id=incident_id)
    if request.user.is_superuser:
        messages.warning(request, "Admin should close tasks from the close action after final review.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if request.user.profile.role != UserProfile.Role.RESPONDER:
        messages.error(request, "Only an assigned responder can change incident status.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    ready, reason = _responder_ready(request.user)
    if not ready:
        messages.warning(request, reason)
        return redirect("disaster_management:profile_edit")
    if not _responder_allowed_for_incident(request.user, incident):
        messages.error(request, "Accept this task first before updating incident status.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    open_enquiry = _open_status_enquiry(incident)
    if open_enquiry:
        messages.warning(request, "Admin requested a status enquiry. Respond using the enquiry response section first.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    is_stale_for_enquiry, stale_days_without_update, _ = _is_incident_stale_for_enquiry(incident)
    if is_stale_for_enquiry:
        messages.warning(
            request,
            f"No incident update for {stale_days_without_update} day(s). Wait for admin enquiry request before posting status update.",
        )
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentStatusUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please choose a valid status and try again."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    next_status = form.cleaned_data["status"]
    status_steps, allowed_statuses = _responder_status_steps(incident)
    step_values = {step["value"] for step in status_steps}
    if next_status not in step_values:
        messages.error(request, "Please use the status step buttons for field updates.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if next_status == incident.status:
        messages.warning(request, f"Incident is already marked as {incident.get_status_display()}.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if next_status not in allowed_statuses:
        messages.error(request, "Please move status using the next available step.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    note_value = (form.cleaned_data.get("note") or "").strip()
    if next_status in {Incident.Status.STABILIZED, Incident.Status.ESCALATED} and len(note_value) < 8:
        messages.error(request, "Please add a short note (minimum 8 characters) for Stabilized/Escalated updates.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    on_scene_distance_km = None
    if next_status == Incident.Status.ON_SCENE:
        location_ok, location_error, on_scene_distance_km, live_lat, live_lon = _validate_on_scene_location(
            request.user,
            incident,
            live_latitude_raw=request.POST.get("on_scene_latitude"),
            live_longitude_raw=request.POST.get("on_scene_longitude"),
            require_live_location=True,
        )
        if not location_ok:
            messages.error(request, location_error)
            return redirect("disaster_management:incident_detail", incident_id=incident.id)
        responder_profile = getattr(request.user, "responder_profile", None)
        if responder_profile:
            responder_profile.latitude = live_lat
            responder_profile.longitude = live_lon
            responder_profile.last_checkin_at = timezone.now()
            responder_profile.save(update_fields=["latitude", "longitude", "last_checkin_at", "updated_at"])

    incident.status = next_status
    status_update_fields = ["status", "updated_at"]
    if incident.status == Incident.Status.ON_SCENE and not incident.first_response_at:
        incident.first_response_at = timezone.now()
        status_update_fields.append("first_response_at")
    incident.save(update_fields=status_update_fields)
    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note=note_value,
    )
    if incident.status == Incident.Status.ESCALATED:
        escalation_assignments_opened, escalation_notifications_sent = _dispatch_escalation_support(incident, radius_km=30.0)
        IncidentUpdate.objects.create(
            incident=incident,
            actor=request.user,
            status=incident.status,
            note=(
                f"Escalation broadcast sent to {escalation_notifications_sent} responders within 30 km. "
                f"{escalation_assignments_opened} assignment(s) opened/reopened."
            ),
        )
        messages.warning(
            request,
            f"Incident escalated. Alert sent to {escalation_notifications_sent} responders within 30 km.",
        )
    elif incident.status == Incident.Status.ON_SCENE and on_scene_distance_km is not None:
        messages.success(request, f"Incident status updated. On Scene verified at {on_scene_distance_km:.2f} km from incident.")
    else:
        messages.success(request, "Incident status updated.")
    if incident.reporter:
        _log_notification(
            incident=incident,
            user=incident.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject=f"Update on your report #{incident.id}",
            message=f"Current update: {_reporter_status_label(incident.status)}.",
        )
    _notify_admins(
        incident=incident,
        subject=f"Incident #{incident.id} updated by responder",
        message=f"{request.user.username} set status to {incident.get_status_display()}.",
    )
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_verify_view(request, incident_id):
    if not request.user.is_superuser:
        messages.error(request, "Only admin can verify incidents.")
        return redirect("disaster_management:incident_detail", incident_id=incident_id)
    incident = get_object_or_404(Incident, id=incident_id)
    if incident.status != Incident.Status.PENDING:
        messages.warning(request, "This incident has already been reviewed.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    incident.status = Incident.Status.VERIFIED
    incident.verified_by = request.user
    incident.verified_at = timezone.now()
    incident.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note="Incident verified by admin.",
    )
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        _notify_admins(
            incident=incident,
            subject=f"Emergency incident verified #{incident.id}",
            message=f"Emergency incident #{incident.id} is verified and dispatch is in progress.",
        )
    emergency_fallback_used = False
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        assignments_created, emergency_fallback_used = _dispatch_emergency_with_fallback(
            incident,
            nearby_radius_km=10.0,
            limit=8,
        )
    else:
        assignments_created = _dispatch_nearest_responders(incident, limit=6)
    if incident.report_mode == Incident.ReportMode.EMERGENCY and assignments_created:
        incident.status = Incident.Status.DISPATCHED
        incident.dispatched_at = timezone.now()
        incident.save(update_fields=["status", "dispatched_at", "updated_at"])
        IncidentUpdate.objects.create(
            incident=incident,
            actor=request.user,
            status=incident.status,
            note=f"Dispatched to {assignments_created} responders.",
        )
    elif incident.report_mode == Incident.ReportMode.NORMAL and assignments_created:
        IncidentUpdate.objects.create(
            incident=incident,
            actor=request.user,
            status=incident.status,
            note=f"Accepted by admin. Alert sent to {assignments_created} nearby responders.",
        )
    if incident.reporter:
        reporter_message = "Your report was approved. Nearby response teams can now accept this case."
        if incident.report_mode == Incident.ReportMode.EMERGENCY:
            reporter_message = "Your emergency report was approved and response teams are being contacted."
        _log_notification(
            incident=incident,
            user=incident.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject=f"Report #{incident.id} approved",
            message=reporter_message,
        )
    warnings, _ = _run_consequence_intelligence_and_notify(incident)
    if warnings:
        messages.warning(request, "Nearby safety warning was sent based on consequence prediction.")
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        if assignments_created:
            if emergency_fallback_used:
                messages.success(
                    request,
                    "Emergency incident approved. No nearby responder found, so the closest available responder was alerted.",
                )
            else:
                messages.success(request, "Emergency incident approved and dispatch started.")
        else:
            messages.success(request, "Emergency incident approved. No nearby responder was available.")
    elif assignments_created:
        messages.success(request, "Incident approved. Nearby responders can now accept the task.")
    else:
        messages.success(request, "Incident approved, but no nearby responder was available.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_reject_view(request, incident_id):
    if not request.user.is_superuser:
        messages.error(request, "Only admin can reject incidents.")
        return redirect("disaster_management:incident_detail", incident_id=incident_id)
    incident = get_object_or_404(Incident, id=incident_id)
    incident.status = Incident.Status.REJECTED
    incident.verified_by = request.user
    incident.verified_at = timezone.now()
    incident.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note="Incident rejected after verification review.",
    )
    if incident.reporter:
        _log_notification(
            incident=incident,
            user=incident.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject=f"Update on your report #{incident.id}",
            message="We could not approve this report after review.",
        )
    _notify_admins(
        incident=incident,
        subject=f"Incident #{incident.id} rejected",
        message=f"{request.user.username} rejected incident #{incident.id} after review.",
    )
    messages.warning(request, "Incident rejected.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def assignment_accept_view(request, incident_id):
    ready, reason = _responder_ready(request.user)
    if not ready:
        messages.warning(request, reason)
        return redirect("disaster_management:profile_edit")
    with transaction.atomic():
        assignment = get_object_or_404(
            IncidentAssignment.objects.select_for_update().select_related("incident"),
            incident_id=incident_id,
            responder=request.user,
        )
        incident = assignment.incident
        active_commitment = (
            IncidentAssignment.objects.select_for_update()
            .filter(
                responder=request.user,
                status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            )
            .exclude(incident_id=incident_id)
            .exclude(incident__status__in={Incident.Status.REJECTED, Incident.Status.CLOSED})
            .order_by("-accepted_at", "-created_at")
            .first()
        )
        if active_commitment:
            messages.warning(
                request,
                f"You are already committed to incident #{active_commitment.incident_id}. Complete it before accepting another assignment.",
            )
            return redirect("disaster_management:incident_detail", incident_id=active_commitment.incident_id)
        if incident.status in {Incident.Status.REJECTED, Incident.Status.CLOSED}:
            messages.warning(request, "This incident is no longer open for responder acceptance.")
            return redirect("disaster_management:incident_detail", incident_id=incident_id)
        if assignment.status == IncidentAssignment.AssignmentStatus.CANCELLED:
            messages.warning(request, "This assignment is no longer active.")
            return redirect("disaster_management:incident_detail", incident_id=incident_id)
        if assignment.status == IncidentAssignment.AssignmentStatus.DECLINED:
            messages.warning(request, "You already declined this assignment and cannot accept it again.")
            return redirect("disaster_management:incident_detail", incident_id=incident_id)
        if assignment.status == IncidentAssignment.AssignmentStatus.ACCEPTED:
            messages.info(request, "You already accepted this assignment.")
            return redirect("disaster_management:incident_detail", incident_id=incident_id)

        assignment.status = IncidentAssignment.AssignmentStatus.ACCEPTED
        assignment.accepted_at = timezone.now()
        assignment.declined_at = None
        assignment.save(update_fields=["status", "accepted_at", "declined_at", "updated_at"])

        incident_update_fields = []
        if incident.status == Incident.Status.VERIFIED:
            incident.status = Incident.Status.DISPATCHED
            incident_update_fields.append("status")
        if incident.dispatched_at is None:
            incident.dispatched_at = timezone.now()
            incident_update_fields.append("dispatched_at")
        if incident_update_fields:
            incident_update_fields.append("updated_at")
            incident.save(update_fields=incident_update_fields)

        IncidentUpdate.objects.create(
            incident=incident,
            actor=request.user,
            status=incident.status,
            note=f"Responder {request.user.username} accepted this assignment.",
        )
        if incident.reporter:
            _log_notification(
                incident=incident,
                user=incident.reporter,
                target_type=NotificationLog.TargetType.REPORTER,
                subject=f"Team accepted your report #{incident.id}",
                message="A response team accepted your report and is handling this case.",
            )
        _notify_admins(
            incident=incident,
            subject=f"Responder accepted incident #{incident.id}",
            message=f"{request.user.username} accepted the incident task.",
        )
    messages.success(request, "Assignment accepted.")
    return redirect("disaster_management:incident_detail", incident_id=incident_id)


@login_required
@require_POST
def assignment_decline_view(request, incident_id):
    ready, reason = _responder_ready(request.user)
    if not ready:
        messages.warning(request, reason)
        return redirect("disaster_management:profile_edit")
    with transaction.atomic():
        assignment = get_object_or_404(
            IncidentAssignment.objects.select_for_update().select_related("incident"),
            incident_id=incident_id,
            responder=request.user,
        )
        incident = assignment.incident
        if incident.status in {Incident.Status.REJECTED, Incident.Status.CLOSED}:
            messages.warning(request, "This incident is no longer open for responder actions.")
            return redirect("disaster_management:incident_detail", incident_id=incident_id)
        if assignment.status == IncidentAssignment.AssignmentStatus.DECLINED:
            messages.warning(request, "You already declined this assignment. Further action is disabled.")
            return redirect("disaster_management:incident_detail", incident_id=incident_id)

        status_changed = assignment.status != IncidentAssignment.AssignmentStatus.DECLINED
        if status_changed:
            assignment.status = IncidentAssignment.AssignmentStatus.DECLINED
            assignment.declined_at = timezone.now()
            assignment.accepted_at = None
            assignment.save(update_fields=["status", "declined_at", "accepted_at", "updated_at"])

            IncidentUpdate.objects.create(
                incident=incident,
                actor=request.user,
                status=incident.status,
                note=f"Responder {request.user.username} declined this assignment.",
            )
            _notify_admins(
                incident=incident,
                subject=f"Responder declined incident #{incident.id}",
                message=f"{request.user.username} declined the assigned incident task.",
            )

        has_accepted_assignment = incident.assignments.filter(status=IncidentAssignment.AssignmentStatus.ACCEPTED).exists()
        redispatched = 0
        if status_changed and not has_accepted_assignment:
            redispatched = _offer_incident_to_next_responders(incident, limit=3, exclude_user_ids={request.user.id})
            if redispatched:
                if incident.status == Incident.Status.VERIFIED:
                    incident.status = Incident.Status.DISPATCHED
                    incident.save(update_fields=["status", "updated_at"])
                if incident.dispatched_at is None:
                    incident.dispatched_at = timezone.now()
                    incident.save(update_fields=["dispatched_at", "updated_at"])
                IncidentUpdate.objects.create(
                    incident=incident,
                    actor=request.user,
                    status=incident.status,
                    note=f"Task was automatically offered to {redispatched} other nearby responder(s).",
                )
        if status_changed and incident.reporter:
            reporter_message = "A response team declined this case. We are finding another nearby team."
            if redispatched:
                reporter_message = f"A response team declined. We alerted {redispatched} other nearby team(s)."
            _log_notification(
                incident=incident,
                user=incident.reporter,
                target_type=NotificationLog.TargetType.REPORTER,
                subject=f"Update on your report #{incident.id}",
                message=reporter_message,
            )

    if redispatched:
        messages.warning(request, f"Assignment declined. Task reassigned to {redispatched} nearby responder(s).")
    else:
        messages.warning(request, "Assignment declined.")
    return redirect("disaster_management:incident_detail", incident_id=incident_id)


@login_required
@require_POST
def incident_final_review_submit_view(request, incident_id):
    incident = get_object_or_404(Incident, id=incident_id)
    if request.user.profile.role != UserProfile.Role.RESPONDER:
        messages.error(request, "Only responders can submit final review.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    ready, reason = _responder_ready(request.user)
    if not ready:
        messages.warning(request, reason)
        return redirect("disaster_management:profile_edit")
    if not _responder_allowed_for_incident(request.user, incident):
        messages.error(request, "Only accepted responder can submit final review.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    final_review_stage_ready = incident.status in {Incident.Status.STABILIZED, Incident.Status.ESCALATED}
    if not final_review_stage_ready:
        messages.warning(request, "Complete field update first. Set status to Stabilized or Escalated before final review.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if incident.status == Incident.Status.CLOSED:
        messages.warning(request, "Incident is already closed.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    existing_review = getattr(incident, "final_review", None)
    if existing_review and existing_review.responder_id != request.user.id:
        messages.error(request, "Final review already submitted by another responder.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if existing_review and existing_review.points_awarded:
        messages.warning(request, "Final review is locked because points already awarded.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentFinalReviewForm(request.POST, request.FILES or None, instance=existing_review)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please update final review details and try again."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    attachments = request.FILES.getlist("attachments")
    if len(attachments) > 8:
        messages.error(request, "You can upload up to 8 final review attachments.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    existing_proof_count = 0
    if existing_review:
        existing_proof_count = existing_review.media.count()
    if not attachments and existing_proof_count == 0:
        messages.error(request, "Please attach at least one photo or video proof in final review.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    review = form.save(commit=False)
    if incident.status != Incident.Status.STABILIZED:
        # Closure recommendation is meaningful only once situation is stabilized.
        review.recommend_close = False
    review.incident = incident
    review.responder = request.user
    review.save()
    for file_obj in attachments:
        IncidentFinalReviewMedia.objects.create(final_review=review, file=file_obj)

    IncidentUpdate.objects.create(
        incident=incident,
        actor=request.user,
        status=incident.status,
        note="Final review submitted by accepted responder.",
    )
    _notify_admins(
        incident=incident,
        subject=f"Final review submitted for incident #{incident.id}",
        message=(
            f"Responder {request.user.username} submitted final review and recommended closure."
            if review.recommend_close
            else f"Responder {request.user.username} submitted final review for admin review."
        ),
    )
    if review.recommend_close and incident.status == Incident.Status.STABILIZED:
        messages.success(request, "Final review submitted and closure recommended. Admin can now close the task.")
    else:
        messages.success(request, "Final review submitted. Admin review is pending.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_fake_report_submit_view(request, incident_id):
    incident = get_object_or_404(Incident, id=incident_id)
    if request.user.profile.role != UserProfile.Role.RESPONDER:
        messages.error(request, "Only responders can report fake incidents.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if not _responder_allowed_for_incident(request.user, incident):
        messages.error(request, "Only accepted responder can report this incident as fake.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if incident.status != Incident.Status.ON_SCENE:
        messages.warning(request, "Fake incident review is allowed only when incident status is On Scene.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if incident.status == Incident.Status.CLOSED:
        messages.warning(request, "Closed incidents cannot be reported as fake.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if incident.fake_reports.filter(responder=request.user).exists():
        messages.warning(request, "You already submitted a fake incident report for this task.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    form = IncidentFakeReportForm(request.POST, request.FILES or None)
    if not form.is_valid():
        messages.error(request, _form_error_message(form, "Please explain why this looks fake."))
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    attachments = request.FILES.getlist("attachments")
    if len(attachments) > 6:
        messages.error(request, "You can upload up to 6 fake-report proofs.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    if not attachments:
        messages.error(request, "Please attach at least one proof file for fake incident review.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)

    assignment_released = False
    with transaction.atomic():
        fake_report = form.save(commit=False)
        fake_report.incident = incident
        fake_report.responder = request.user
        fake_report.save()
        for file_obj in attachments:
            IncidentFakeReportMedia.objects.create(fake_report=fake_report, file=file_obj)

        my_assignment = (
            IncidentAssignment.objects.select_for_update()
            .filter(
                incident=incident,
                responder=request.user,
                status=IncidentAssignment.AssignmentStatus.ACCEPTED,
            )
            .first()
        )
        if my_assignment:
            my_assignment.status = IncidentAssignment.AssignmentStatus.CANCELLED
            my_assignment.accepted_at = None
            my_assignment.declined_at = None
            my_assignment.save(update_fields=["status", "accepted_at", "declined_at", "updated_at"])
            assignment_released = True

        update_note = "Responder flagged this incident as possibly fake and sent proof for admin review."
        if assignment_released:
            update_note += " Assignment was released so responder can accept another incident while admin review is pending."
        IncidentUpdate.objects.create(
            incident=incident,
            actor=request.user,
            status=incident.status,
            note=update_note,
        )
    _notify_admins(
        incident=incident,
        subject=f"Fake incident review requested for #{incident.id}",
        message=f"{request.user.username} reported this incident as potentially fake and attached proofs.",
    )
    if assignment_released:
        messages.warning(
            request,
            "Fake incident report submitted to admin for review. Your task commitment for this incident is released.",
        )
    else:
        messages.warning(request, "Fake incident report submitted to admin for review.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@login_required
@require_POST
def incident_close_view(request, incident_id):
    if not request.user.is_superuser:
        messages.error(request, "Only admin can close incidents.")
        return redirect("disaster_management:incident_detail", incident_id=incident_id)
    incident = get_object_or_404(Incident, id=incident_id)
    if incident.status == Incident.Status.CLOSED:
        messages.info(request, "Incident is already closed.")
        return redirect("disaster_management:incident_detail", incident_id=incident.id)
    final_review = getattr(incident, "final_review", None)
    closure_ready, _, closure_missing_requirements = _closure_requirements(incident, final_review)
    force_close = not closure_ready or _open_status_enquiry(incident) is not None

    points_given = 0
    assignments_released = 0
    enquiries_finalized = 0
    closed_at = timezone.now()

    with transaction.atomic():
        assignments_released = incident.assignments.filter(
            status__in={
                IncidentAssignment.AssignmentStatus.NOTIFIED,
                IncidentAssignment.AssignmentStatus.ACCEPTED,
            }
        ).update(
            status=IncidentAssignment.AssignmentStatus.CANCELLED,
            accepted_at=None,
            declined_at=None,
            updated_at=closed_at,
        )

        enquiries_finalized = incident.status_enquiries.filter(
            status__in={
                IncidentStatusEnquiry.WorkflowStatus.REQUESTED,
                IncidentStatusEnquiry.WorkflowStatus.RESPONDED,
            }
        ).update(
            status=IncidentStatusEnquiry.WorkflowStatus.FINALIZED,
            finalized_by=request.user,
            finalized_at=closed_at,
            final_admin_note="Auto-finalized because admin closed this incident.",
            updated_at=closed_at,
        )

        incident.status = Incident.Status.CLOSED
        incident.closed_at = closed_at
        incident.save(update_fields=["status", "closed_at", "updated_at"])

        close_note = "Incident closed by admin after final review."
        if force_close:
            close_note = "Incident force-closed by admin."
            if closure_missing_requirements:
                close_note += " Pending at closure: " + ", ".join(closure_missing_requirements) + "."
        if enquiries_finalized:
            close_note += f" Finalized {enquiries_finalized} status enquiry record(s)."
        if assignments_released:
            close_note += f" Released {assignments_released} active assignment(s)."
        IncidentUpdate.objects.create(
            incident=incident,
            actor=request.user,
            status=incident.status,
            note=close_note,
        )

        if closure_ready and final_review and not final_review.points_awarded:
            points_given = _response_points_for_incident(incident)
            responder_profile, _ = ResponderProfile.objects.get_or_create(
                user=final_review.responder,
                defaults={
                    "full_name": final_review.responder.get_full_name() or final_review.responder.username,
                    "phone_number": "",
                    "skills": "",
                },
            )
            responder_profile.response_points += points_given
            responder_profile.completed_incidents += 1
            responder_profile.save(update_fields=["response_points", "completed_incidents", "updated_at"])
            final_review.points_awarded = True
            final_review.points_awarded_at = timezone.now()
            final_review.save(update_fields=["points_awarded", "points_awarded_at", "updated_at"])
            _log_notification(
                incident=incident,
                user=final_review.responder,
                target_type=NotificationLog.TargetType.RESPONDER,
                subject=f"RRS points credited for incident #{incident.id}",
                message=f"You earned {points_given} Rapid Response Score (RRS) points.",
            )

    if incident.reporter:
        _log_notification(
            incident=incident,
            user=incident.reporter,
            target_type=NotificationLog.TargetType.REPORTER,
            subject=f"Report #{incident.id} closed",
            message=(
                "Your report has been force-closed by admin."
                if force_close
                else "Your report has been closed after final review."
            ),
        )
    _notify_admins(
        incident=incident,
        subject=f"Incident #{incident.id} closed",
        message=(
            f"{request.user.username} force-closed incident #{incident.id}."
            if force_close
            else f"{request.user.username} closed incident #{incident.id} after final review."
        ),
    )
    if force_close and points_given:
        messages.success(request, f"Incident force-closed. {points_given} RRS points credited to responder.")
    elif force_close:
        messages.success(request, "Incident force-closed successfully.")
    elif points_given:
        messages.success(request, f"Incident closed. {points_given} RRS points credited to responder.")
    else:
        messages.success(request, "Incident closed successfully.")
    return redirect("disaster_management:incident_detail", incident_id=incident.id)


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_GET
def fake_reports_review_list_view(request):
    keyword = (request.GET.get("q") or "").strip()
    queue_query = (
        IncidentFakeReport.objects.select_related(
            "incident",
            "responder",
            "reviewed_by",
            "incident__reporter",
            "incident__reporter__profile",
        )
        .prefetch_related("media")
        .filter(review_status=IncidentFakeReport.ReviewStatus.PENDING)
        .order_by("created_at")
    )
    if keyword:
        queue_filter = (
            Q(reason__icontains=keyword)
            | Q(incident__title__icontains=keyword)
            | Q(responder__username__icontains=keyword)
            | Q(incident__reporter__username__icontains=keyword)
        )
        if keyword.isdigit():
            queue_filter = queue_filter | Q(incident_id=int(keyword))
        queue_query = queue_query.filter(queue_filter)

    paginator = Paginator(queue_query, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    reports = page_obj.object_list

    summary_counts = {
        "pending": IncidentFakeReport.objects.filter(review_status=IncidentFakeReport.ReviewStatus.PENDING).count(),
        "reviewed": IncidentFakeReport.objects.filter(review_status=IncidentFakeReport.ReviewStatus.REVIEWED).count(),
        "warning": IncidentFakeReport.objects.filter(
            review_status=IncidentFakeReport.ReviewStatus.REVIEWED,
            admin_action=IncidentFakeReport.AdminAction.WARNING,
        ).count(),
        "blocked": IncidentFakeReport.objects.filter(
            review_status=IncidentFakeReport.ReviewStatus.REVIEWED,
            admin_action=IncidentFakeReport.AdminAction.BLOCK,
        ).count(),
    }

    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    pagination_query = query_params.urlencode()

    return render(
        request,
        "dashboard/fake_reports_review.html",
        {
            "reports": reports,
            "page_obj": page_obj,
            "keyword": keyword,
            "summary_counts": summary_counts,
            "pagination_query": pagination_query,
        },
    )


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_GET
def fake_reports_history_view(request):
    action_filter = (request.GET.get("admin_action") or "").strip()
    keyword = (request.GET.get("q") or "").strip()
    from_date_raw = (request.GET.get("from_date") or "").strip()
    to_date_raw = (request.GET.get("to_date") or "").strip()

    history_query = (
        IncidentFakeReport.objects.select_related(
            "incident",
            "responder",
            "reviewed_by",
            "incident__reporter",
            "incident__reporter__profile",
        )
        .prefetch_related("media")
        .filter(review_status=IncidentFakeReport.ReviewStatus.REVIEWED)
        .order_by("-reviewed_at", "-updated_at")
    )
    if action_filter in dict(IncidentFakeReport.AdminAction.choices):
        history_query = history_query.filter(admin_action=action_filter)
    if keyword:
        history_filter = (
            Q(reason__icontains=keyword)
            | Q(admin_note__icontains=keyword)
            | Q(incident__title__icontains=keyword)
            | Q(responder__username__icontains=keyword)
            | Q(incident__reporter__username__icontains=keyword)
        )
        if keyword.isdigit():
            history_filter = history_filter | Q(incident_id=int(keyword))
        history_query = history_query.filter(history_filter)

    from_date = _parse_date_or_none(from_date_raw)
    to_date = _parse_date_or_none(to_date_raw)
    if from_date_raw and from_date is None:
        messages.warning(request, "From date was ignored because format is invalid. Use YYYY-MM-DD.")
    if to_date_raw and to_date is None:
        messages.warning(request, "To date was ignored because format is invalid. Use YYYY-MM-DD.")
    if from_date:
        history_query = history_query.filter(reviewed_at__date__gte=from_date)
    if to_date:
        history_query = history_query.filter(reviewed_at__date__lte=to_date)

    paginator = Paginator(history_query, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    reports = page_obj.object_list

    summary_counts = {
        "reviewed": IncidentFakeReport.objects.filter(review_status=IncidentFakeReport.ReviewStatus.REVIEWED).count(),
        "warning": IncidentFakeReport.objects.filter(
            review_status=IncidentFakeReport.ReviewStatus.REVIEWED,
            admin_action=IncidentFakeReport.AdminAction.WARNING,
        ).count(),
        "blocked": IncidentFakeReport.objects.filter(
            review_status=IncidentFakeReport.ReviewStatus.REVIEWED,
            admin_action=IncidentFakeReport.AdminAction.BLOCK,
        ).count(),
        "no_action": IncidentFakeReport.objects.filter(
            review_status=IncidentFakeReport.ReviewStatus.REVIEWED,
            admin_action=IncidentFakeReport.AdminAction.NO_ACTION,
        ).count(),
    }

    query_params = request.GET.copy()
    if "page" in query_params:
        query_params.pop("page")
    pagination_query = query_params.urlencode()

    return render(
        request,
        "dashboard/fake_reports_history.html",
        {
            "reports": reports,
            "page_obj": page_obj,
            "summary_counts": summary_counts,
            "action_filter": action_filter,
            "action_choices": IncidentFakeReport.AdminAction.choices,
            "keyword": keyword,
            "from_date": from_date_raw,
            "to_date": to_date_raw,
            "pagination_query": pagination_query,
        },
    )


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_POST
def fake_report_action_view(request, report_id):
    return_to = (request.POST.get("return_to") or "").strip().lower()
    redirect_target = "disaster_management:fake_reports_history" if return_to == "history" else "disaster_management:fake_reports_review"
    action = request.POST.get("admin_action", IncidentFakeReport.AdminAction.NO_ACTION)
    note = request.POST.get("admin_note", "").strip()
    purge_reporter_incidents = str(request.POST.get("purge_reporter_incidents", "")).lower() in {"1", "true", "on", "yes"}
    valid_actions = {choice[0] for choice in IncidentFakeReport.AdminAction.choices}
    if action not in valid_actions:
        messages.error(request, "Invalid admin action.")
        return redirect(redirect_target)

    deleted_incidents = 0
    incident_closed = False
    assignments_cancelled = 0
    with transaction.atomic():
        report = get_object_or_404(
            IncidentFakeReport.objects.select_for_update().select_related("incident__reporter", "responder"),
            id=report_id,
        )
        previous_action = report.admin_action

        report.review_status = IncidentFakeReport.ReviewStatus.REVIEWED
        report.admin_action = action
        report.admin_note = note
        report.reviewed_by = request.user
        report.reviewed_at = timezone.now()
        report.save(update_fields=["review_status", "admin_action", "admin_note", "reviewed_by", "reviewed_at", "updated_at"])

        reporter = report.incident.reporter
        if reporter:
            profile = reporter.profile
            if action == IncidentFakeReport.AdminAction.WARNING:
                warning_applied_now = previous_action != IncidentFakeReport.AdminAction.WARNING
                if warning_applied_now:
                    profile.warning_count += 1
                message_text = f"We issued a warning for report #{report.incident_id}."
                if profile.warning_count >= 3 and not profile.is_blocked:
                    profile.is_blocked = True
                    profile.blocked_at = timezone.now()
                    profile.blocked_reason = "Blocked after repeated fake incident submissions."
                    reporter.is_active = False
                    reporter.save(update_fields=["is_active"])
                    message_text += " Your account is now blocked due to repeated fake reports."
                profile_fields = ["updated_at"]
                if warning_applied_now:
                    profile_fields.append("warning_count")
                if profile.is_blocked:
                    profile_fields.extend(["is_blocked", "blocked_at", "blocked_reason"])
                profile.save(update_fields=list(dict.fromkeys(profile_fields)))
                _log_notification(
                    incident=report.incident,
                    user=reporter,
                    target_type=NotificationLog.TargetType.REPORTER,
                    subject=f"Important notice for report #{report.incident_id}",
                    message=message_text,
                )
            elif action == IncidentFakeReport.AdminAction.BLOCK:
                profile.is_blocked = True
                profile.blocked_at = timezone.now()
                profile.blocked_reason = note or "Blocked after fake incident verification."
                profile.save(update_fields=["is_blocked", "blocked_at", "blocked_reason", "updated_at"])
                reporter.is_active = False
                reporter.save(update_fields=["is_active"])
                if purge_reporter_incidents:
                    deleted_incidents = Incident.objects.filter(reporter=reporter).count()
                    if deleted_incidents:
                        Incident.objects.filter(reporter=reporter).delete()
                else:
                    _log_notification(
                        incident=report.incident,
                        user=reporter,
                        target_type=NotificationLog.TargetType.REPORTER,
                        subject=f"Account update for report #{report.incident_id}",
                        message="Your account was blocked after review of this report.",
                    )
            else:
                _log_notification(
                    incident=report.incident,
                    user=reporter,
                    target_type=NotificationLog.TargetType.REPORTER,
                    subject=f"Review completed for report #{report.incident_id}",
                    message="We reviewed this report and did not take action on your account.",
                )

        close_after_review = not (action == IncidentFakeReport.AdminAction.BLOCK and purge_reporter_incidents)
        if close_after_review and report.incident.status != Incident.Status.CLOSED:
            report.incident.status = Incident.Status.CLOSED
            report.incident.closed_at = timezone.now()
            report.incident.save(update_fields=["status", "closed_at", "updated_at"])
            assignments_cancelled = report.incident.assignments.filter(
                status__in={
                    IncidentAssignment.AssignmentStatus.NOTIFIED,
                    IncidentAssignment.AssignmentStatus.ACCEPTED,
                }
            ).update(
                status=IncidentAssignment.AssignmentStatus.CANCELLED,
                accepted_at=None,
                declined_at=None,
                updated_at=timezone.now(),
            )
            IncidentUpdate.objects.create(
                incident=report.incident,
                actor=request.user,
                status=report.incident.status,
                note=f"Admin reviewed fake report ({report.get_admin_action_display()}) and closed this incident.",
            )
            incident_closed = True

        if not (action == IncidentFakeReport.AdminAction.BLOCK and purge_reporter_incidents):
            responder_message = f"Admin action: {report.get_admin_action_display()}."
            if incident_closed:
                responder_message += " Incident closed after fake-report verification."
            _log_notification(
                incident=report.incident,
                user=report.responder,
                target_type=NotificationLog.TargetType.RESPONDER,
                subject=f"Fake report reviewed for incident #{report.incident_id}",
                message=responder_message,
            )

    if deleted_incidents:
        messages.success(request, f"Fake incident report reviewed. {deleted_incidents} reported incident(s) were deleted.")
    elif incident_closed:
        if assignments_cancelled:
            messages.success(
                request,
                f"Fake incident report reviewed. Incident closed and {assignments_cancelled} active assignment(s) were released.",
            )
        else:
            messages.success(request, "Fake incident report reviewed. Incident closed after admin action.")
    else:
        messages.success(request, "Fake incident report reviewed.")
    return redirect(redirect_target)


@login_required
@require_GET
def responder_rankings_view(request):
    responders = (
        ResponderProfile.objects.select_related("user")
        .filter(user__is_active=True, user__profile__verification_status=UserProfile.VerificationStatus.APPROVED)
        .order_by("-response_points", "-completed_incidents", "full_name")
    )
    my_rank = None
    if hasattr(request.user, "responder_profile"):
        my_points = request.user.responder_profile.response_points
        my_rank = ResponderProfile.objects.filter(response_points__gt=my_points, user__is_active=True).count() + 1
    return render(
        request,
        "dashboard/responder_rankings.html",
        {
            "responders": responders[:300],
            "my_rank": my_rank,
        },
    )


@login_required
def profile_edit_view(request):
    form_type = request.POST.get("form_type", "") if request.method == "POST" else ""
    is_password_form_submit = form_type == "password_change"
    is_upgrade_request_submit = form_type == "upgrade_request"
    is_profile_form_submit = request.method == "POST" and form_type in {"", "profile_update"}

    user_form = UserUpdateForm(
        request.POST if is_profile_form_submit else None,
        instance=request.user,
        user_instance=request.user,
    )
    profile_form = UserProfileUpdateForm(
        request.POST if is_profile_form_submit else None,
        request.FILES if is_profile_form_submit else None,
        instance=request.user.profile,
        is_superuser=request.user.is_superuser,
    )
    password_form = PasswordChangeForm(
        request.user,
        request.POST if is_password_form_submit else None,
    )
    latest_upgrade_request = None
    upgrade_request_form = None
    password_field_styles = {
        "old_password": "Current password",
        "new_password1": "New password",
        "new_password2": "Confirm new password",
    }
    for field_name, placeholder in password_field_styles.items():
        password_form.fields[field_name].widget.attrs.update(
            {
                "class": "form-control bg-dark text-light border-secondary",
                "placeholder": placeholder,
            }
        )
    open_password_modal = False
    responder_form = None
    is_responder = request.user.profile.role == UserProfile.Role.RESPONDER
    can_request_responder_upgrade = (
        not request.user.is_superuser
        and request.user.profile.role == UserProfile.Role.REPORTER
        and request.user.profile.verification_status == UserProfile.VerificationStatus.APPROVED
    )
    responder_instance = None
    if is_responder:
        responder_instance, _ = ResponderProfile.objects.get_or_create(
            user=request.user,
            defaults={
                "full_name": request.user.get_full_name() or request.user.username,
                "phone_number": "",
                "skills": "",
            },
        )
        responder_form = ResponderProfileForm(
            request.POST if is_profile_form_submit else None,
            instance=responder_instance,
        )
    elif can_request_responder_upgrade:
        latest_upgrade_request = (
            ResponderUpgradeRequest.objects.filter(user=request.user)
            .select_related("reviewed_by")
            .order_by("-created_at")
            .first()
        )
        upgrade_request_form = ResponderUpgradeRequestForm(
            request.POST if is_upgrade_request_submit else None,
            initial={
                "full_name": request.user.get_full_name() or request.user.username,
            },
        )

    if is_password_form_submit:
        if password_form.is_valid():
            updated_user = password_form.save()
            update_session_auth_hash(request, updated_user)
            messages.success(request, "Password updated successfully.")
            return redirect("disaster_management:profile_edit")
        open_password_modal = True
        messages.error(request, _form_error_message(password_form, fallback="Please check your password fields."))
    elif is_upgrade_request_submit:
        if not can_request_responder_upgrade:
            messages.error(request, "Only approved reporter accounts can request responder access.")
            return redirect("disaster_management:profile_edit")
        if ResponderUpgradeRequest.objects.filter(
            user=request.user,
            status=ResponderUpgradeRequest.Status.PENDING,
        ).exists():
            messages.warning(request, "You already have a pending responder access request.")
            return redirect("disaster_management:profile_edit")
        if upgrade_request_form is None:
            messages.error(request, "Unable to load responder request form. Please try again.")
        elif not upgrade_request_form.is_valid():
            messages.error(
                request,
                _form_error_message(upgrade_request_form, fallback="Please update the responder request form and try again."),
            )
        else:
            upgrade_request = upgrade_request_form.save(commit=False)
            upgrade_request.user = request.user
            upgrade_request.save()
            messages.success(request, "Responder access request submitted. Admin review is pending.")
            return redirect("disaster_management:profile_edit")
    elif request.method == "POST":
        user_ok = user_form.is_valid()
        profile_ok = profile_form.is_valid()
        responder_ok = True if responder_form is None else responder_form.is_valid()
        if user_ok and profile_ok and responder_ok:
            user_form.save()
            profile_form.save()
            if responder_form is not None:
                responder = responder_form.save(commit=False)
                responder.last_checkin_at = timezone.now()
                responder.save()
                messages.success(request, "Responder check-in updated for today.")
            messages.success(request, "Profile updated successfully.")
            return redirect("disaster_management:profile_edit")
        messages.error(
            request,
            _form_error_message(user_form)
            if not user_ok
            else (_form_error_message(profile_form) if not profile_ok else _form_error_message(responder_form)),
        )

    profile_photo_url = None
    if hasattr(request.user, "profile") and request.user.profile.profile_photo:
        profile_photo_url = request.user.profile.profile_photo.url

    return render(
        request,
        "dashboard/profile_edit.html",
        {
            "user_form": user_form,
            "profile_form": profile_form,
            "profile_photo_url": profile_photo_url,
            "display_name": request.user.get_full_name() or request.user.username,
            "role_label": "Admin" if request.user.is_superuser else request.user.profile.get_role_display(),
            "responder_form": responder_form,
            "password_form": password_form,
            "open_password_modal": open_password_modal,
            "can_request_responder_upgrade": can_request_responder_upgrade,
            "upgrade_request_form": upgrade_request_form,
            "latest_upgrade_request": latest_upgrade_request,
        },
    )


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_GET
def verification_list_view(request):
    pending_profiles = (
        UserProfile.objects.select_related("user", "user__responder_profile")
        .filter(
            role=UserProfile.Role.RESPONDER,
            verification_status=UserProfile.VerificationStatus.PENDING,
            user__is_superuser=False,
        )
        .order_by("created_at")
    )
    pending_upgrade_requests = (
        ResponderUpgradeRequest.objects.select_related("user", "reviewed_by")
        .filter(status=ResponderUpgradeRequest.Status.PENDING)
        .order_by("created_at")
    )
    return render(
        request,
        "dashboard/verification_list.html",
        {
            "pending_profiles": pending_profiles,
            "pending_upgrade_requests": pending_upgrade_requests,
        },
    )


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_POST
def approve_user_view(request, user_id):
    user = get_object_or_404(
        get_user_model().objects.select_related("profile"),
        id=user_id,
        is_superuser=False,
        profile__role=UserProfile.Role.RESPONDER,
    )
    if user.profile.verification_status != UserProfile.VerificationStatus.PENDING:
        messages.warning(request, f"Responder signup for {user.username} is already reviewed.")
        return redirect("disaster_management:verification_list")
    user.is_active = True
    user.save(update_fields=["is_active"])

    user.profile.verification_status = UserProfile.VerificationStatus.APPROVED
    user.profile.verified_at = timezone.now()
    user.profile.verified_by = request.user
    user.profile.save(update_fields=["verification_status", "verified_at", "verified_by"])
    if user.profile.role == UserProfile.Role.RESPONDER:
        responder_profile, _ = ResponderProfile.objects.get_or_create(
            user=user,
            defaults={
                "full_name": user.get_full_name() or user.username,
                "phone_number": "",
                "skills": "",
            },
        )
        if not responder_profile.is_verified:
            responder_profile.is_verified = True
            responder_profile.save(update_fields=["is_verified", "updated_at"])
    messages.success(request, f"Approved user: {user.username}")
    return redirect("disaster_management:verification_list")


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_POST
def reject_user_view(request, user_id):
    user = get_object_or_404(
        get_user_model().objects.select_related("profile"),
        id=user_id,
        is_superuser=False,
        profile__role=UserProfile.Role.RESPONDER,
    )
    if user.profile.verification_status != UserProfile.VerificationStatus.PENDING:
        messages.warning(request, f"Responder signup for {user.username} is already reviewed.")
        return redirect("disaster_management:verification_list")
    user.is_active = False
    user.save(update_fields=["is_active"])

    user.profile.verification_status = UserProfile.VerificationStatus.REJECTED
    user.profile.verified_at = timezone.now()
    user.profile.verified_by = request.user
    user.profile.save(update_fields=["verification_status", "verified_at", "verified_by"])
    responder_profile = getattr(user, "responder_profile", None)
    if responder_profile and responder_profile.is_verified:
        responder_profile.is_verified = False
        responder_profile.save(update_fields=["is_verified", "updated_at"])
    messages.warning(request, f"Rejected user: {user.username}")
    return redirect("disaster_management:verification_list")


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_POST
def approve_responder_upgrade_view(request, upgrade_request_id):
    upgrade_request = get_object_or_404(
        ResponderUpgradeRequest.objects.select_related("user", "user__profile"),
        id=upgrade_request_id,
    )
    if upgrade_request.status != ResponderUpgradeRequest.Status.PENDING:
        messages.warning(request, "This responder upgrade request is already reviewed.")
        return redirect("disaster_management:verification_list")

    admin_note = (request.POST.get("admin_note") or "").strip()
    with transaction.atomic():
        upgrade_request.status = ResponderUpgradeRequest.Status.APPROVED
        upgrade_request.admin_note = admin_note
        upgrade_request.reviewed_by = request.user
        upgrade_request.reviewed_at = timezone.now()
        upgrade_request.save(update_fields=["status", "admin_note", "reviewed_by", "reviewed_at", "updated_at"])

        user = upgrade_request.user
        profile = user.profile
        profile.role = UserProfile.Role.RESPONDER
        profile.save(update_fields=["role", "updated_at"])

        responder_profile, _ = ResponderProfile.objects.get_or_create(
            user=user,
            defaults={
                "full_name": upgrade_request.full_name,
                "phone_number": upgrade_request.phone_number,
                "skills": upgrade_request.skills,
            },
        )
        responder_profile.full_name = upgrade_request.full_name
        responder_profile.phone_number = upgrade_request.phone_number
        responder_profile.skills = upgrade_request.skills
        if profile.verification_status == UserProfile.VerificationStatus.APPROVED:
            responder_profile.is_verified = True
        responder_profile.save(update_fields=["full_name", "phone_number", "skills", "is_verified", "updated_at"])

    messages.success(request, f"Responder access approved for {upgrade_request.user.username}.")
    return redirect("disaster_management:verification_list")


@user_passes_test(lambda user: user.is_authenticated and user.is_superuser)
@require_POST
def reject_responder_upgrade_view(request, upgrade_request_id):
    upgrade_request = get_object_or_404(ResponderUpgradeRequest, id=upgrade_request_id)
    if upgrade_request.status != ResponderUpgradeRequest.Status.PENDING:
        messages.warning(request, "This responder upgrade request is already reviewed.")
        return redirect("disaster_management:verification_list")

    upgrade_request.status = ResponderUpgradeRequest.Status.REJECTED
    upgrade_request.admin_note = (request.POST.get("admin_note") or "").strip()
    upgrade_request.reviewed_by = request.user
    upgrade_request.reviewed_at = timezone.now()
    upgrade_request.save(update_fields=["status", "admin_note", "reviewed_by", "reviewed_at", "updated_at"])

    messages.warning(request, f"Responder access request rejected for {upgrade_request.user.username}.")
    return redirect("disaster_management:verification_list")


@require_POST
def logout_view(request):
    if request.user.is_authenticated and hasattr(request.user, "profile") and request.user.profile.role == UserProfile.Role.RESPONDER:
        responder = getattr(request.user, "responder_profile", None)
        if responder:
            responder.is_available = False
            responder.latitude = None
            responder.longitude = None
            responder.last_checkin_at = None
            responder.save(update_fields=["is_available", "latitude", "longitude", "last_checkin_at", "updated_at"])
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect("disaster_management:login")


@require_POST
def report_incident(request):
    return JsonResponse({"message": "Use dashboard incident reporting page or API v2 endpoint."}, status=200)


@require_POST
def set_responder_availability(request):
    if not request.user.is_authenticated:
        return JsonResponse({"message": "Authentication required."}, status=401)
    if request.user.profile.role != UserProfile.Role.RESPONDER:
        return JsonResponse({"message": "Only responders can update availability."}, status=403)

    responder, _ = ResponderProfile.objects.get_or_create(
        user=request.user,
        defaults={"full_name": request.user.get_full_name() or request.user.username, "phone_number": "", "skills": ""},
    )
    is_available = request.POST.get("is_available", "").lower() in {"1", "true", "yes", "on"}
    lat_raw = (request.POST.get("latitude") or "").strip()
    lon_raw = (request.POST.get("longitude") or "").strip()

    if (lat_raw and not lon_raw) or (lon_raw and not lat_raw):
        return JsonResponse({"message": "Both latitude and longitude are required together."}, status=400)

    lat_value = responder.latitude
    lon_value = responder.longitude
    if lat_raw and lon_raw:
        try:
            lat_value = float(lat_raw)
            lon_value = float(lon_raw)
        except ValueError:
            return JsonResponse({"message": "Latitude/longitude format is invalid."}, status=400)
        if lat_value < -90 or lat_value > 90:
            return JsonResponse({"message": "Latitude must be between -90 and 90."}, status=400)
        if lon_value < -180 or lon_value > 180:
            return JsonResponse({"message": "Longitude must be between -180 and 180."}, status=400)

    if is_available and (lat_value is None or lon_value is None):
        return JsonResponse(
            {"message": "Location is required when availability is ON. Add latitude and longitude or turn availability OFF."},
            status=400,
        )

    responder.is_available = is_available
    responder.latitude = lat_value
    responder.longitude = lon_value
    responder.last_checkin_at = timezone.now()
    responder.save(update_fields=["is_available", "latitude", "longitude", "last_checkin_at", "updated_at"])
    if is_available:
        return JsonResponse({"message": "Availability is ON and location/check-in updated."}, status=200)
    return JsonResponse({"message": "Availability is OFF. You will not receive tasks."}, status=200)
