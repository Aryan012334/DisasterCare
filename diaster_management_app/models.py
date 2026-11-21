from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.db import models
from django.dispatch import receiver
from django.utils import timezone


# ---------------------------------------------------------------------------
# India Geographic Reference Models
# ---------------------------------------------------------------------------

class IndianState(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_union_territory = models.BooleanField(default=False)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class IndianDistrict(models.Model):
    state = models.ForeignKey(IndianState, on_delete=models.CASCADE, related_name="districts")
    name = models.CharField(max_length=100)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lgd_code = models.CharField(max_length=20, blank=True, default='', help_text='LGD district code')

    class Meta:
        ordering = ("name",)
        unique_together = ("state", "name")

    def __str__(self):
        return f"{self.name}, {self.state.name}"


# ---------------------------------------------------------------------------
# Disaster Risk Assessment
# ---------------------------------------------------------------------------

class DisasterRiskZone(models.Model):
    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        HIGH = "high", "High"
        VERY_HIGH = "very_high", "Very High"

    class HazardType(models.TextChoices):
        FLOOD = "flood", "Flood"
        CYCLONE = "cyclone", "Cyclone"
        EARTHQUAKE = "earthquake", "Earthquake"
        DROUGHT = "drought", "Drought"
        LANDSLIDE = "landslide", "Landslide"
        HEATWAVE = "heatwave", "Heatwave"
        FOREST_FIRE = "forest_fire", "Forest Fire"
        TSUNAMI = "tsunami", "Tsunami"
        INDUSTRIAL = "industrial", "Industrial Hazard"
        OTHER = "other", "Other"

    state = models.ForeignKey(IndianState, on_delete=models.SET_NULL, null=True, blank=True, related_name="risk_zones")
    district = models.ForeignKey(IndianDistrict, on_delete=models.SET_NULL, null=True, blank=True, related_name="risk_zones")
    name = models.CharField(max_length=255)
    hazard_type = models.CharField(max_length=30, choices=HazardType.choices, default=HazardType.FLOOD)
    risk_level = models.CharField(max_length=20, choices=RiskLevel.choices, default=RiskLevel.MODERATE)
    risk_score = models.FloatField(default=50.0, help_text="0-100 composite risk score")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    radius_km = models.FloatField(default=10.0)
    population_at_risk = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    historical_incidents = models.PositiveIntegerField(default=0)
    last_disaster_year = models.PositiveIntegerField(null=True, blank=True)
    preventive_measures = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-risk_score",)

    def __str__(self):
        return f"{self.name} ({self.get_hazard_type_display()} - {self.get_risk_level_display()})"


class MitigationTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    risk_zone = models.ForeignKey(DisasterRiskZone, on_delete=models.CASCADE, related_name="mitigation_tasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    assigned_to = models.CharField(max_length=255, blank=True, help_text="Department or agency responsible")
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title} [{self.get_status_display()}]"


# ---------------------------------------------------------------------------
# Early Warning System
# ---------------------------------------------------------------------------

class EarlyWarning(models.Model):
    class Severity(models.TextChoices):
        ADVISORY = "advisory", "Advisory"
        WATCH = "watch", "Watch"
        WARNING = "warning", "Warning"
        EXTREME = "extreme", "Extreme Alert"

    class DisasterType(models.TextChoices):
        FLOOD = "flood", "Flood"
        CYCLONE = "cyclone", "Cyclone"
        EARTHQUAKE = "earthquake", "Earthquake"
        DROUGHT = "drought", "Drought"
        LANDSLIDE = "landslide", "Landslide"
        HEATWAVE = "heatwave", "Heatwave"
        FOREST_FIRE = "forest_fire", "Forest Fire"
        TSUNAMI = "tsunami", "Tsunami"
        STORM = "storm", "Thunderstorm/Lightning"
        COLD_WAVE = "cold_wave", "Cold Wave"
        OTHER = "other", "Other"

    class Source(models.TextChoices):
        NDMA = "ndma", "NDMA SACHET"
        IMD = "imd", "IMD (India Meteorological Dept)"
        CWC = "cwc", "CWC (Central Water Commission)"
        INCOIS = "incois", "INCOIS (Tsunami Alerts)"
        MANUAL = "manual", "Manual Entry by Admin"
        OTHER = "other", "Other Official Source"

    title = models.CharField(max_length=255)
    disaster_type = models.CharField(max_length=30, choices=DisasterType.choices)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.ADVISORY)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    source_url = models.URLField(blank=True)
    affected_states = models.ManyToManyField(IndianState, blank=True, related_name="warnings")
    affected_districts = models.ManyToManyField(IndianDistrict, blank=True, related_name="warnings")
    description = models.TextField()
    safety_instructions = models.TextField(blank=True)
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    issued_by = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="issued_warnings"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-issued_at",)

    def __str__(self):
        return f"{self.title} [{self.get_severity_display()}]"

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at


# ---------------------------------------------------------------------------
# Shelter Management
# ---------------------------------------------------------------------------

class Shelter(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FULL = "full", "Full"
        CLOSED = "closed", "Closed"
        STANDBY = "standby", "On Standby"

    name = models.CharField(max_length=255)
    address = models.TextField()
    state = models.ForeignKey(IndianState, on_delete=models.SET_NULL, null=True, blank=True, related_name="shelters")
    district = models.ForeignKey(IndianDistrict, on_delete=models.SET_NULL, null=True, blank=True, related_name="shelters")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    capacity = models.PositiveIntegerField(default=0)
    current_occupancy = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.STANDBY)
    has_food = models.BooleanField(default=False)
    has_water = models.BooleanField(default=False)
    has_electricity = models.BooleanField(default=False)
    has_sanitation = models.BooleanField(default=False)
    has_medical = models.BooleanField(default=False)
    is_accessible = models.BooleanField(default=True, help_text="Accessible for differently-abled persons")
    contact_phone = models.CharField(max_length=20, blank=True)
    managed_by = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.current_occupancy}/{self.capacity})"

    @property
    def available_capacity(self):
        return max(0, self.capacity - self.current_occupancy)

    @property
    def occupancy_percent(self):
        if self.capacity == 0:
            return 0
        return min(100, int(self.current_occupancy * 100 / self.capacity))


class EvacuationPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    incident = models.ForeignKey(
        "Incident", on_delete=models.CASCADE, null=True, blank=True, related_name="evacuation_plans"
    )
    title = models.CharField(max_length=255)
    state = models.ForeignKey(IndianState, on_delete=models.SET_NULL, null=True, blank=True, related_name="evacuation_plans")
    district = models.ForeignKey(IndianDistrict, on_delete=models.SET_NULL, null=True, blank=True, related_name="evacuation_plans")
    description = models.TextField(blank=True)
    affected_population = models.PositiveIntegerField(default=0)
    shelters = models.ManyToManyField(Shelter, blank=True, related_name="evacuation_plans")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="evacuation_plans"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title} [{self.get_status_display()}]"


# ---------------------------------------------------------------------------
# Relief Resource Management
# ---------------------------------------------------------------------------

class ResourceCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    icon = models.CharField(max_length=50, blank=True, default="fa-box")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "Resource Categories"

    def __str__(self):
        return self.name


class ReliefResource(models.Model):
    class Unit(models.TextChoices):
        UNITS = "units", "Units"
        KG = "kg", "Kilograms"
        LITRES = "litres", "Litres"
        PACKETS = "packets", "Packets"
        BOXES = "boxes", "Boxes"
        SETS = "sets", "Sets"

    category = models.ForeignKey(ResourceCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="resources")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=20, choices=Unit.choices, default=Unit.UNITS)
    quantity_available = models.PositiveIntegerField(default=0)
    quantity_reserved = models.PositiveIntegerField(default=0)
    minimum_stock = models.PositiveIntegerField(default=0, help_text="Alert when stock falls below this")
    state = models.ForeignKey(IndianState, on_delete=models.SET_NULL, null=True, blank=True, related_name="resources")
    district = models.ForeignKey(IndianDistrict, on_delete=models.SET_NULL, null=True, blank=True, related_name="resources")
    location_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.quantity_available} {self.unit})"

    @property
    def quantity_free(self):
        return max(0, self.quantity_available - self.quantity_reserved)

    @property
    def is_low_stock(self):
        return self.quantity_available <= self.minimum_stock


class ResourceRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        DISPATCHED = "dispatched", "Dispatched"
        DELIVERED = "delivered", "Delivered"

    incident = models.ForeignKey(
        "Incident", on_delete=models.SET_NULL, null=True, blank=True, related_name="resource_requests"
    )
    resource = models.ForeignKey(ReliefResource, on_delete=models.CASCADE, related_name="requests")
    requested_by = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="resource_requests"
    )
    quantity_requested = models.PositiveIntegerField()
    quantity_approved = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    purpose = models.TextField(blank=True)
    admin_note = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_resource_requests"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Request {self.id}: {self.resource.name} x{self.quantity_requested} [{self.get_status_display()}]"


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
        CYCLONE = "cyclone", "Cyclone"
        DROUGHT = "drought", "Drought"
        HEATWAVE = "heatwave", "Heatwave"
        FOREST_FIRE = "forest_fire", "Forest Fire"
        TSUNAMI = "tsunami", "Tsunami"
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
    state = models.ForeignKey(
        "IndianState", on_delete=models.SET_NULL, null=True, blank=True, related_name="incidents"
    )
    district = models.ForeignKey(
        "IndianDistrict", on_delete=models.SET_NULL, null=True, blank=True, related_name="incidents"
    )
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
    state = models.ForeignKey(
        "IndianState", on_delete=models.SET_NULL, null=True, blank=True, related_name="responders"
    )
    district = models.ForeignKey(
        "IndianDistrict", on_delete=models.SET_NULL, null=True, blank=True, related_name="responders"
    )
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


# ---------------------------------------------------------------------------
# Infrastructure Registry
# ---------------------------------------------------------------------------

class InfrastructureAsset(models.Model):
    class Category(models.TextChoices):
        HOSPITAL = 'hospital', 'Hospital'
        SCHOOL = 'school', 'School'
        BRIDGE = 'bridge', 'Bridge'
        DAM = 'dam', 'Dam'
        ROAD = 'road', 'Major Road'
        WATER_FACILITY = 'water', 'Water Facility'
        FIRE_STATION = 'fire_station', 'Fire Station'
        POLICE_STATION = 'police_station', 'Police Station'
        EMERGENCY_CENTER = 'emergency', 'Emergency Center'
        POWER_PLANT = 'power', 'Power Plant'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    state = models.ForeignKey('IndianState', on_delete=models.SET_NULL, null=True, blank=True, related_name='infrastructure')
    district = models.ForeignKey('IndianDistrict', on_delete=models.SET_NULL, null=True, blank=True, related_name='infrastructure')
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    address = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(default=0, help_text='Beds/seats/MW/etc.')
    is_operational = models.BooleanField(default=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    is_sensitive = models.BooleanField(default=False, help_text='Restrict to admin-only if True')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return f'{self.name} ({self.get_category_display()})'


# ---------------------------------------------------------------------------
# Damage Assessment
# ---------------------------------------------------------------------------

class DamageAssessment(models.Model):
    class Severity(models.TextChoices):
        MINOR = 'minor', 'Minor'
        MODERATE = 'moderate', 'Moderate'
        SEVERE = 'severe', 'Severe'
        CATASTROPHIC = 'catastrophic', 'Catastrophic'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SUBMITTED = 'submitted', 'Submitted'
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'

    incident = models.ForeignKey('Incident', on_delete=models.CASCADE, related_name='damage_assessments')
    assessed_by = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='damage_assessments')
    state = models.ForeignKey('IndianState', on_delete=models.SET_NULL, null=True, blank=True)
    district = models.ForeignKey('IndianDistrict', on_delete=models.SET_NULL, null=True, blank=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.MODERATE)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    homes_damaged = models.PositiveIntegerField(default=0)
    homes_destroyed = models.PositiveIntegerField(default=0)
    roads_damaged_km = models.FloatField(default=0.0)
    crops_damaged_hectares = models.FloatField(default=0.0)
    public_infra_damaged = models.TextField(blank=True)
    estimated_loss_lakhs = models.FloatField(default=0.0, help_text='Estimated loss in Indian Rupees (Lakhs)')
    description = models.TextField(blank=True)
    verified_by = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True, related_name='verified_damage_assessments')
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'Damage Assessment #{self.id} for Incident {self.incident_id}'


# ---------------------------------------------------------------------------
# Location Subscription
# ---------------------------------------------------------------------------

class LocationSubscription(models.Model):
    """User subscription for location-based warning notifications."""
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='location_subscriptions')
    state = models.ForeignKey('IndianState', on_delete=models.CASCADE, null=True, blank=True, related_name='subscribers')
    district = models.ForeignKey('IndianDistrict', on_delete=models.CASCADE, null=True, blank=True, related_name='subscribers')
    notify_in_app = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'state', 'district')
        ordering = ('-created_at',)

    def __str__(self):
        loc = self.district.name if self.district else (self.state.name if self.state else 'All India')
        return f'{self.user.username} subscribed to {loc}'
