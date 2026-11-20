from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.db import models
from django.dispatch import receiver
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Incident(TimeStampedModel):
    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        HIGH = "high", "High"
        SEVERE = "severe", "Severe"

    class ReportMode(models.TextChoices):
        NORMAL = "normal", "Normal"
        EMERGENCY = "emergency", "Emergency"

    class IncidentType(models.TextChoices):
        FIRE = "fire", "Fire"
        FLOOD = "flood", "Flood"
        EARTHQUAKE = "earthquake", "Earthquake"
        LANDSLIDE = "landslide", "Landslide"
        ROAD_ACCIDENT = "road_accident", "Road Accident"
        MEDICAL = "medical", "Medical Emergency"
        OTHER = "other", "Other"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        DISPATCHED = "dispatched", "Dispatched"
        ON_SCENE = "on_scene", "On Scene"
        STABILIZED = "stabilized", "Stabilized"
        ESCALATED = "escalated", "Escalated"
        CLOSED = "closed", "Closed"

    reporter = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_incidents",
    )
    title = models.CharField(max_length=255)
    report_mode = models.CharField(max_length=20, choices=ReportMode.choices, default=ReportMode.NORMAL)
    incident_type = models.CharField(max_length=30, choices=IncidentType.choices, default=IncidentType.OTHER)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MEDIUM)
    description = models.TextField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    people_affected = models.PositiveIntegerField(default=0)
    contact_phone = models.CharField(max_length=20, blank=True)
    affected_radius_km = models.FloatField(default=1.0)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    ai_confidence = models.FloatField(default=0.0)
    ai_risk_score = models.FloatField(default=0.0)
    ai_consequence_summary = models.TextField(blank=True)
    is_duplicate_suspected = models.BooleanField(default=False)
    duplicate_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="possible_duplicates",
    )
    verified_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_incidents",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.status})"


class IncidentMedia(TimeStampedModel):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="media")
    file = models.FileField(upload_to="incident_media/")
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"Media {self.id} for Incident {self.incident_id}"


class IncidentUpdate(TimeStampedModel):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="updates")
    actor = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Incident.Status.choices)
    note = models.TextField(blank=True)

    def __str__(self):
        return f"Update {self.status} for Incident {self.incident_id}"


class IncidentStatusEnquiry(TimeStampedModel):
    class WorkflowStatus(models.TextChoices):
        REQUESTED = "requested", "Requested"
        RESPONDED = "responded", "Responder Responded"
        FINALIZED = "finalized", "Finalized"

    class OverdueAction(models.TextChoices):
        NONE = "none", "No Action"
        WARNING = "warning", "Warning Issued"
        SUSPEND = "suspend", "Responder Suspended"

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="status_enquiries")
    requested_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_incident_status_enquiries",
    )
    target_responder = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_incident_status_enquiries",
    )
    request_note = models.TextField(blank=True)
    requested_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(max_length=20, choices=WorkflowStatus.choices, default=WorkflowStatus.REQUESTED)

    responded_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responded_incident_status_enquiries",
    )
    responder_current_status = models.CharField(max_length=20, choices=Incident.Status.choices, blank=True)
    responder_note = models.TextField(blank=True)
    responder_recommend_finalize = models.BooleanField(default=False)
    responded_at = models.DateTimeField(null=True, blank=True)

    finalized_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finalized_incident_status_enquiries",
    )
    final_admin_note = models.TextField(blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    overdue_action = models.CharField(max_length=20, choices=OverdueAction.choices, default=OverdueAction.NONE)
    overdue_action_note = models.TextField(blank=True)
    overdue_action_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="overdue_action_status_enquiries",
    )
    overdue_action_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-requested_at", "-created_at")

    def __str__(self):
        return f"StatusEnquiry {self.incident_id} ({self.status})"


class ResponderProfile(TimeStampedModel):
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="responder_profile",
        null=True,
        blank=True,
    )
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20)
    skills = models.TextField(blank=True)
    is_available = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_checkin_at = models.DateTimeField(null=True, blank=True)
    response_points = models.PositiveIntegerField(default=0)
    completed_incidents = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.full_name


class UserProfile(TimeStampedModel):
    class Role(models.TextChoices):
        REPORTER = "reporter", "Reporter"
        RESPONDER = "responder", "Responder"

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.REPORTER)
    profile_photo = models.FileField(upload_to="profile_photos/", null=True, blank=True)
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_users",
    )
    warning_count = models.PositiveIntegerField(default=0)
    is_blocked = models.BooleanField(default=False)
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_reason = models.TextField(blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class PasswordResetOTP(TimeStampedModel):
    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="password_reset_otps",
    )
    email = models.EmailField()
    otp_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    attempts_remaining = models.PositiveSmallIntegerField(default=5)
    send_count = models.PositiveSmallIntegerField(default=1)
    last_sent_at = models.DateTimeField(default=timezone.now)
    is_used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "is_used"]),
            models.Index(fields=["user", "is_used", "created_at"]),
        ]
        ordering = ("-created_at",)

    def __str__(self):
        return f"PasswordResetOTP {self.email} ({'used' if self.is_used else 'active'})"


class ResponderUpgradeRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="responder_upgrade_requests",
    )
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20)
    skills = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    admin_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_responder_upgrade_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"ResponderUpgradeRequest {self.user_id} ({self.status})"


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    """
    Every non-admin auth user gets a role profile automatically.
    Superusers are treated as admins through is_superuser.
    """
    if created and not hasattr(instance, "profile"):
        verification_status = UserProfile.VerificationStatus.APPROVED if instance.is_superuser else UserProfile.VerificationStatus.PENDING
        verified_at = timezone.now() if instance.is_superuser else None
        UserProfile.objects.create(
            user=instance,
            verification_status=verification_status,
            verified_at=verified_at,
            verified_by=instance if instance.is_superuser else None,
        )


class IncidentAssignment(TimeStampedModel):
    class AssignmentStatus(models.TextChoices):
        NOTIFIED = "notified", "Notified"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="assignments")
    responder = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="incident_assignments")
    status = models.CharField(max_length=20, choices=AssignmentStatus.choices, default=AssignmentStatus.NOTIFIED)
    distance_km = models.FloatField(default=0.0)
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("incident", "responder")

    def __str__(self):
        return f"Assignment {self.incident_id} -> {self.responder_id} ({self.status})"


class NotificationLog(TimeStampedModel):
    class Channel(models.TextChoices):
        IN_APP = "in_app", "In App"
        EMAIL = "email", "Email"

    class TargetType(models.TextChoices):
        REPORTER = "reporter", "Reporter"
        RESPONDER = "responder", "Responder"
        ADMIN = "admin", "Admin"

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="notification_logs")
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="notification_logs")
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.IN_APP)
    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    delivered = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Notification {self.id} ({self.target_type})"


class IncidentFinalReview(TimeStampedModel):
    incident = models.OneToOneField(Incident, on_delete=models.CASCADE, related_name="final_review")
    responder = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="incident_final_reviews")
    summary = models.TextField()
    verification_notes = models.TextField(blank=True)
    recommend_close = models.BooleanField(default=False)
    points_awarded = models.BooleanField(default=False)
    points_awarded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"FinalReview for Incident {self.incident_id}"


class IncidentRiskPrediction(TimeStampedModel):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="risk_predictions")
    horizon_hours = models.PositiveIntegerField()
    forecast_for = models.DateTimeField()
    risk_score = models.FloatField(default=0.0)
    risk_level = models.CharField(max_length=20, choices=Incident.RiskLevel.choices, default=Incident.RiskLevel.LOW)
    advisory = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("incident", "horizon_hours")
        ordering = ("horizon_hours",)

    def __str__(self):
        return f"RiskForecast Incident {self.incident_id} +{self.horizon_hours}h"


class IncidentFinalReviewMedia(TimeStampedModel):
    final_review = models.ForeignKey(IncidentFinalReview, on_delete=models.CASCADE, related_name="media")
    file = models.FileField(upload_to="incident_final_review_media/")
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"FinalReviewMedia {self.id} for Incident {self.final_review.incident_id}"


class IncidentFakeReport(TimeStampedModel):
    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        REVIEWED = "reviewed", "Reviewed"

    class AdminAction(models.TextChoices):
        NO_ACTION = "no_action", "No Action"
        WARNING = "warning", "Warning Issued"
        BLOCK = "block", "Reporter Blocked"

    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="fake_reports")
    responder = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name="fake_incident_reports")
    reason = models.TextField()
    review_status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    admin_action = models.CharField(max_length=20, choices=AdminAction.choices, default=AdminAction.NO_ACTION)
    admin_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_fake_incident_reports",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("incident", "responder")

    def __str__(self):
        return f"FakeReport Incident {self.incident_id} by {self.responder_id}"


class IncidentFakeReportMedia(TimeStampedModel):
    fake_report = models.ForeignKey(IncidentFakeReport, on_delete=models.CASCADE, related_name="media")
    file = models.FileField(upload_to="incident_fake_report_media/")
    caption = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"FakeReportMedia {self.id} for Incident {self.fake_report.incident_id}"
