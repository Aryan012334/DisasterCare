"""
DisasterCare – India-wide disaster management views.
Covers: Risk Zones, Early Warnings, Shelters, Resources, India Map, National Dashboard.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import (
    DisasterRiskZone, EarlyWarning, EvacuationPlan, Incident,
    IndianDistrict, IndianState, MitigationTask, ReliefResource,
    ResourceCategory, ResourceRequest, Shelter, UserProfile,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(user):
    return user.is_authenticated and user.is_superuser


def _require_login_and_active(request):
    if not request.user.is_authenticated:
        return redirect("disaster_management:login")
    return None


# ---------------------------------------------------------------------------
# India National Map
# ---------------------------------------------------------------------------

@login_required
def india_map_view(request):
    states = IndianState.objects.all().order_by("name")
    risk_zones = DisasterRiskZone.objects.filter(is_active=True).select_related("state", "district")
    active_warnings = EarlyWarning.objects.filter(is_active=True).order_by("-issued_at")[:10]
    shelters = Shelter.objects.filter(status__in=[Shelter.Status.ACTIVE, Shelter.Status.STANDBY]).select_related("state")

    return render(request, "dashboard/india_map.html", {
        "states": states,
        "risk_zones": risk_zones,
        "active_warnings": active_warnings,
        "shelters": shelters,
        "active_menu": "india_map",
    })


@login_required
@require_GET
def india_map_data_view(request):
    """JSON endpoint for the India map layer data.

    Admins and responders receive full incident coordinates and addresses.
    Regular reporters and other authenticated users receive only non-sensitive
    incident fields (incident_type, severity, status) — no lat/lon or address.
    Shelters, risk zones, and warnings are always returned in full as they are
    public-safety information.
    """
    is_privileged = (
        request.user.is_superuser
        or (
            hasattr(request.user, "profile")
            and request.user.profile.role == UserProfile.Role.RESPONDER
        )
    )

    active_statuses = [
        Incident.Status.PENDING, Incident.Status.VERIFIED,
        Incident.Status.DISPATCHED, Incident.Status.ON_SCENE,
        Incident.Status.STABILIZED, Incident.Status.ESCALATED,
    ]

    if is_privileged:
        incidents_qs = Incident.objects.filter(
            status__in=active_statuses
        ).values("id", "title", "incident_type", "severity", "latitude", "longitude", "status", "address")
        incidents_data = list(incidents_qs)
    else:
        incidents_qs = Incident.objects.filter(
            status__in=active_statuses
        ).values("id", "incident_type", "severity", "status")
        incidents_data = list(incidents_qs)

    # Risk zones
    risk_qs = DisasterRiskZone.objects.filter(is_active=True).values(
        "id", "name", "hazard_type", "risk_level", "risk_score",
        "latitude", "longitude", "radius_km", "population_at_risk",
    )

    # Shelters — always public (they are public facilities)
    shelter_qs = Shelter.objects.filter(
        status__in=[Shelter.Status.ACTIVE, Shelter.Status.STANDBY, Shelter.Status.FULL]
    ).values("id", "name", "status", "latitude", "longitude", "capacity", "current_occupancy")

    # Warnings — always public
    warning_qs = EarlyWarning.objects.filter(is_active=True).values(
        "id", "title", "disaster_type", "severity", "issued_at"
    )

    return JsonResponse({
        "incidents": incidents_data,
        "risk_zones": [
            {**r, "latitude": float(r["latitude"]), "longitude": float(r["longitude"])}
            for r in risk_qs
        ],
        "shelters": [
            {**s, "latitude": float(s["latitude"]), "longitude": float(s["longitude"])}
            for s in shelter_qs
        ],
        "warnings": [
            {**w, "issued_at": w["issued_at"].isoformat() if w["issued_at"] else None}
            for w in warning_qs
        ],
    })


# ---------------------------------------------------------------------------
# Disaster Risk Zones
# ---------------------------------------------------------------------------

@login_required
def risk_zone_list_view(request):
    qs = DisasterRiskZone.objects.filter(is_active=True).select_related("state", "district")

    hazard = request.GET.get("hazard", "")
    risk = request.GET.get("risk", "")
    state_id = request.GET.get("state", "")
    q = request.GET.get("q", "")

    if hazard:
        qs = qs.filter(hazard_type=hazard)
    if risk:
        qs = qs.filter(risk_level=risk)
    if state_id:
        qs = qs.filter(state_id=state_id)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))

    paginator = Paginator(qs.order_by("-risk_score"), 15)
    page = paginator.get_page(request.GET.get("page"))

    # summary counts for overview cards
    total_zones = DisasterRiskZone.objects.filter(is_active=True).count()
    very_high_count = DisasterRiskZone.objects.filter(is_active=True, risk_level="very_high").count()
    high_count = DisasterRiskZone.objects.filter(is_active=True, risk_level="high").count()

    return render(request, "dashboard/risk_zone_list.html", {
        "page_obj": page,
        "hazard_choices": DisasterRiskZone.HazardType.choices,
        "risk_choices": DisasterRiskZone.RiskLevel.choices,
        "states": IndianState.objects.all(),
        "selected_hazard": hazard,
        "selected_risk": risk,
        "selected_state": state_id,
        "q": q,
        "total_zones": total_zones,
        "very_high_count": very_high_count,
        "high_count": high_count,
        "active_menu": "risk_zones",
    })


@login_required
def risk_zone_detail_view(request, zone_id):
    zone = get_object_or_404(DisasterRiskZone, pk=zone_id)
    tasks = zone.mitigation_tasks.all().order_by("-created_at")
    nearby_incidents = Incident.objects.filter(
        incident_type=zone.hazard_type,
        status__in=[
            Incident.Status.PENDING, Incident.Status.VERIFIED,
            Incident.Status.DISPATCHED, Incident.Status.ON_SCENE,
        ]
    ).order_by("-created_at")[:5]

    return render(request, "dashboard/risk_zone_detail.html", {
        "zone": zone,
        "tasks": tasks,
        "nearby_incidents": nearby_incidents,
        "task_status_choices": MitigationTask.Status.choices,
        "active_menu": "risk_zones",
    })


@login_required
def risk_zone_create_view(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admins can create risk zones.")
        return redirect("disaster_management:risk_zone_list")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        hazard_type = request.POST.get("hazard_type", "flood")
        risk_level = request.POST.get("risk_level", "moderate")
        state_id = request.POST.get("state_id") or None
        district_id = request.POST.get("district_id") or None
        lat = request.POST.get("latitude", "")
        lon = request.POST.get("longitude", "")
        radius = request.POST.get("radius_km", "10")
        population = request.POST.get("population_at_risk", "0")
        description = request.POST.get("description", "")
        preventive = request.POST.get("preventive_measures", "")

        if not name or not lat or not lon:
            messages.error(request, "Name, latitude, and longitude are required.")
        else:
            try:
                risk_score_map = {"low": 20, "moderate": 45, "high": 70, "very_high": 90}
                DisasterRiskZone.objects.create(
                    name=name,
                    hazard_type=hazard_type,
                    risk_level=risk_level,
                    risk_score=risk_score_map.get(risk_level, 50),
                    state_id=state_id,
                    district_id=district_id,
                    latitude=float(lat),
                    longitude=float(lon),
                    radius_km=float(radius),
                    population_at_risk=int(population),
                    description=description,
                    preventive_measures=preventive,
                )
                messages.success(request, f"Risk zone '{name}' created.")
                return redirect("disaster_management:risk_zone_list")
            except (ValueError, TypeError) as e:
                messages.error(request, f"Invalid data: {e}")

    return render(request, "dashboard/risk_zone_form.html", {
        "states": IndianState.objects.all(),
        "hazard_choices": DisasterRiskZone.HazardType.choices,
        "risk_choices": DisasterRiskZone.RiskLevel.choices,
        "active_menu": "risk_zones",
        "form_title": "Add Risk Zone",
    })


@login_required
def mitigation_task_create_view(request, zone_id):
    if not _is_admin(request.user):
        messages.error(request, "Only admins can add mitigation tasks.")
        return redirect("disaster_management:risk_zone_detail", zone_id=zone_id)

    zone = get_object_or_404(DisasterRiskZone, pk=zone_id)
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        priority = request.POST.get("priority", "medium")
        assigned_to = request.POST.get("assigned_to", "")
        due_date = request.POST.get("due_date") or None
        description = request.POST.get("description", "")
        if not title:
            messages.error(request, "Task title is required.")
        else:
            MitigationTask.objects.create(
                risk_zone=zone,
                title=title,
                priority=priority,
                assigned_to=assigned_to,
                due_date=due_date,
                description=description,
            )
            messages.success(request, f"Mitigation task '{title}' added.")
        return redirect("disaster_management:risk_zone_detail", zone_id=zone_id)

    return redirect("disaster_management:risk_zone_detail", zone_id=zone_id)


@login_required
@require_POST
def mitigation_task_update_view(request, task_id):
    if not _is_admin(request.user):
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
    task = get_object_or_404(MitigationTask, pk=task_id)
    new_status = request.POST.get("status")
    progress = request.POST.get("progress_percent")
    notes = request.POST.get("notes", "")
    if new_status in dict(MitigationTask.Status.choices):
        task.status = new_status
        if new_status == MitigationTask.Status.COMPLETED:
            task.completed_at = timezone.now()
            task.progress_percent = 100
    if progress is not None:
        try:
            task.progress_percent = max(0, min(100, int(progress)))
        except ValueError:
            pass
    if notes:
        task.notes = notes
    task.save()
    messages.success(request, f"Task '{task.title}' updated.")
    return redirect("disaster_management:risk_zone_detail", zone_id=task.risk_zone_id)


# ---------------------------------------------------------------------------
# Early Warnings
# ---------------------------------------------------------------------------

@login_required
def warning_list_view(request):
    qs = EarlyWarning.objects.prefetch_related("affected_states").order_by("-issued_at")

    active_only = request.GET.get("active_only", "1")
    disaster_type = request.GET.get("disaster_type", "")
    severity = request.GET.get("severity", "")
    q = request.GET.get("q", "")

    if active_only == "1":
        qs = qs.filter(is_active=True)
    if disaster_type:
        qs = qs.filter(disaster_type=disaster_type)
    if severity:
        qs = qs.filter(severity=severity)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))

    paginator = Paginator(qs, 15)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "dashboard/warning_list.html", {
        "page_obj": page,
        "disaster_type_choices": EarlyWarning.DisasterType.choices,
        "severity_choices": EarlyWarning.Severity.choices,
        "selected_type": disaster_type,
        "selected_severity": severity,
        "active_only": active_only,
        "q": q,
        "total_active": EarlyWarning.objects.filter(is_active=True).count(),
        "extreme_count": EarlyWarning.objects.filter(is_active=True, severity="extreme").count(),
        "active_menu": "warnings",
    })


@login_required
def warning_detail_view(request, warning_id):
    warning = get_object_or_404(EarlyWarning, pk=warning_id)
    return render(request, "dashboard/warning_detail.html", {
        "warning": warning,
        "active_menu": "warnings",
    })


@login_required
def warning_create_view(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admins can issue warnings.")
        return redirect("disaster_management:warning_list")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        disaster_type = request.POST.get("disaster_type", "other")
        severity = request.POST.get("severity", "advisory")
        source = request.POST.get("source", "manual")
        source_url = request.POST.get("source_url", "")
        description = request.POST.get("description", "").strip()
        safety_instructions = request.POST.get("safety_instructions", "")
        expires_in_hours = request.POST.get("expires_in_hours", "")
        state_ids = request.POST.getlist("state_ids")

        if not title or not description:
            messages.error(request, "Title and description are required.")
        else:
            expires_at = None
            if expires_in_hours:
                try:
                    expires_at = timezone.now() + __import__("datetime").timedelta(hours=float(expires_in_hours))
                except (ValueError, TypeError):
                    pass

            warning = EarlyWarning.objects.create(
                title=title,
                disaster_type=disaster_type,
                severity=severity,
                source=source,
                source_url=source_url,
                description=description,
                safety_instructions=safety_instructions,
                expires_at=expires_at,
                is_active=True,
                is_verified=True,
                issued_by=request.user,
            )
            if state_ids:
                warning.affected_states.set(IndianState.objects.filter(id__in=state_ids))

            messages.success(request, f"Warning '{title}' issued.")
            return redirect("disaster_management:warning_detail", warning_id=warning.id)

    return render(request, "dashboard/warning_form.html", {
        "states": IndianState.objects.all(),
        "disaster_type_choices": EarlyWarning.DisasterType.choices,
        "severity_choices": EarlyWarning.Severity.choices,
        "source_choices": EarlyWarning.Source.choices,
        "active_menu": "warnings",
        "form_title": "Issue New Warning",
    })


@login_required
@require_POST
def warning_deactivate_view(request, warning_id):
    if not _is_admin(request.user):
        messages.error(request, "Forbidden.")
        return redirect("disaster_management:warning_list")
    warning = get_object_or_404(EarlyWarning, pk=warning_id)
    warning.is_active = False
    warning.save(update_fields=["is_active", "updated_at"])
    messages.success(request, f"Warning '{warning.title}' deactivated.")
    return redirect("disaster_management:warning_list")


# ---------------------------------------------------------------------------
# Shelters
# ---------------------------------------------------------------------------

@login_required
def shelter_list_view(request):
    qs = Shelter.objects.select_related("state", "district").all()

    state_id = request.GET.get("state", "")
    status_filter = request.GET.get("status", "")
    q = request.GET.get("q", "")

    if state_id:
        qs = qs.filter(state_id=state_id)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(address__icontains=q))

    paginator = Paginator(qs.order_by("name"), 20)
    page = paginator.get_page(request.GET.get("page"))

    total_capacity = Shelter.objects.aggregate(c=Sum("capacity"))["c"] or 0
    total_occupancy = Shelter.objects.aggregate(c=Sum("current_occupancy"))["c"] or 0
    active_count = Shelter.objects.filter(status=Shelter.Status.ACTIVE).count()

    return render(request, "dashboard/shelter_list.html", {
        "page_obj": page,
        "states": IndianState.objects.all(),
        "status_choices": Shelter.Status.choices,
        "selected_state": state_id,
        "selected_status": status_filter,
        "q": q,
        "total_capacity": total_capacity,
        "total_occupancy": total_occupancy,
        "active_count": active_count,
        "active_menu": "shelters",
    })


@login_required
def shelter_detail_view(request, shelter_id):
    shelter = get_object_or_404(Shelter, pk=shelter_id)
    plans = shelter.evacuation_plans.select_related("incident", "state").order_by("-created_at")[:5]
    return render(request, "dashboard/shelter_detail.html", {
        "shelter": shelter,
        "evacuation_plans": plans,
        "active_menu": "shelters",
    })


@login_required
def shelter_create_view(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admins can add shelters.")
        return redirect("disaster_management:shelter_list")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        address = request.POST.get("address", "").strip()
        lat = request.POST.get("latitude", "")
        lon = request.POST.get("longitude", "")
        capacity = request.POST.get("capacity", "0")
        state_id = request.POST.get("state_id") or None
        district_id = request.POST.get("district_id") or None
        managed_by = request.POST.get("managed_by", "")
        contact_phone = request.POST.get("contact_phone", "")
        has_food = request.POST.get("has_food") == "on"
        has_water = request.POST.get("has_water") == "on"
        has_electricity = request.POST.get("has_electricity") == "on"
        has_sanitation = request.POST.get("has_sanitation") == "on"
        has_medical = request.POST.get("has_medical") == "on"
        is_accessible = request.POST.get("is_accessible") == "on"

        if not name or not lat or not lon:
            messages.error(request, "Name, latitude and longitude are required.")
        else:
            try:
                Shelter.objects.create(
                    name=name,
                    address=address,
                    latitude=float(lat),
                    longitude=float(lon),
                    capacity=int(capacity),
                    state_id=state_id,
                    district_id=district_id,
                    managed_by=managed_by,
                    contact_phone=contact_phone,
                    has_food=has_food,
                    has_water=has_water,
                    has_electricity=has_electricity,
                    has_sanitation=has_sanitation,
                    has_medical=has_medical,
                    is_accessible=is_accessible,
                    status=Shelter.Status.STANDBY,
                )
                messages.success(request, f"Shelter '{name}' added.")
                return redirect("disaster_management:shelter_list")
            except (ValueError, TypeError) as e:
                messages.error(request, f"Invalid data: {e}")

    return render(request, "dashboard/shelter_form.html", {
        "states": IndianState.objects.all(),
        "status_choices": Shelter.Status.choices,
        "active_menu": "shelters",
        "form_title": "Add Shelter",
    })


@login_required
@require_POST
def shelter_update_occupancy_view(request, shelter_id):
    if not _is_admin(request.user):
        messages.error(request, "Forbidden.")
        return redirect("disaster_management:shelter_detail", shelter_id=shelter_id)
    shelter = get_object_or_404(Shelter, pk=shelter_id)
    try:
        new_occ = int(request.POST.get("current_occupancy", shelter.current_occupancy))
        new_status = request.POST.get("status", shelter.status)
        shelter.current_occupancy = max(0, min(shelter.capacity, new_occ))
        if new_status in dict(Shelter.Status.choices):
            shelter.status = new_status
        if shelter.current_occupancy >= shelter.capacity:
            shelter.status = Shelter.Status.FULL
        shelter.save(update_fields=["current_occupancy", "status", "updated_at"])
        messages.success(request, "Shelter occupancy updated.")
    except (ValueError, TypeError):
        messages.error(request, "Invalid occupancy value.")
    return redirect("disaster_management:shelter_detail", shelter_id=shelter_id)


# ---------------------------------------------------------------------------
# Evacuation Plans
# ---------------------------------------------------------------------------

@login_required
def evacuation_plan_list_view(request):
    qs = EvacuationPlan.objects.select_related("incident", "state", "district", "created_by").all()
    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)
    paginator = Paginator(qs.order_by("-created_at"), 15)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "dashboard/evacuation_list.html", {
        "page_obj": page,
        "status_choices": EvacuationPlan.Status.choices,
        "selected_status": status_filter,
        "active_menu": "evacuation",
    })


@login_required
def evacuation_plan_detail_view(request, plan_id):
    plan = get_object_or_404(EvacuationPlan, pk=plan_id)
    return render(request, "dashboard/evacuation_detail.html", {
        "plan": plan,
        "shelters": plan.shelters.all(),
        "active_menu": "evacuation",
    })


@login_required
def evacuation_plan_create_view(request):
    if not _is_admin(request.user):
        messages.error(request, "Only admins can create evacuation plans.")
        return redirect("disaster_management:evacuation_list")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "")
        affected_population = request.POST.get("affected_population", "0")
        state_id = request.POST.get("state_id") or None
        district_id = request.POST.get("district_id") or None
        incident_id = request.POST.get("incident_id") or None
        shelter_ids = request.POST.getlist("shelter_ids")

        if not title:
            messages.error(request, "Title is required.")
        else:
            try:
                plan = EvacuationPlan.objects.create(
                    title=title,
                    description=description,
                    affected_population=int(affected_population),
                    state_id=state_id,
                    district_id=district_id,
                    incident_id=incident_id,
                    created_by=request.user,
                    status=EvacuationPlan.Status.DRAFT,
                )
                if shelter_ids:
                    plan.shelters.set(Shelter.objects.filter(id__in=shelter_ids))
                messages.success(request, f"Evacuation plan '{title}' created.")
                return redirect("disaster_management:evacuation_detail", plan_id=plan.id)
            except Exception as e:
                messages.error(request, f"Error: {e}")

    return render(request, "dashboard/evacuation_form.html", {
        "states": IndianState.objects.all(),
        "shelters": Shelter.objects.filter(status__in=[Shelter.Status.ACTIVE, Shelter.Status.STANDBY]),
        "incidents": Incident.objects.filter(
            status__in=[Incident.Status.VERIFIED, Incident.Status.DISPATCHED, Incident.Status.ON_SCENE]
        ).order_by("-created_at")[:30],
        "active_menu": "evacuation",
        "form_title": "Create Evacuation Plan",
    })


@login_required
@require_POST
def evacuation_plan_activate_view(request, plan_id):
    if not _is_admin(request.user):
        messages.error(request, "Forbidden.")
        return redirect("disaster_management:evacuation_detail", plan_id=plan_id)
    plan = get_object_or_404(EvacuationPlan, pk=plan_id)
    plan.status = EvacuationPlan.Status.ACTIVE
    plan.started_at = timezone.now()
    plan.save(update_fields=["status", "started_at", "updated_at"])
    # Activate assigned shelters
    plan.shelters.filter(status=Shelter.Status.STANDBY).update(status=Shelter.Status.ACTIVE)
    messages.success(request, f"Evacuation plan '{plan.title}' activated.")
    return redirect("disaster_management:evacuation_detail", plan_id=plan_id)


# ---------------------------------------------------------------------------
# Relief Resources
# ---------------------------------------------------------------------------

@login_required
def resource_list_view(request):
    qs = ReliefResource.objects.select_related("category", "state", "district").all()

    category_id = request.GET.get("category", "")
    state_id = request.GET.get("state", "")
    low_stock = request.GET.get("low_stock", "")
    q = request.GET.get("q", "")

    if category_id:
        qs = qs.filter(category_id=category_id)
    if state_id:
        qs = qs.filter(state_id=state_id)
    if low_stock == "1":
        from django.db.models import F
        qs = qs.filter(quantity_available__lte=F("minimum_stock"))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))

    paginator = Paginator(qs.order_by("name"), 20)
    page = paginator.get_page(request.GET.get("page"))

    total_resources = ReliefResource.objects.count()
    low_stock_count = sum(1 for r in ReliefResource.objects.all() if r.is_low_stock)

    return render(request, "dashboard/resource_list.html", {
        "page_obj": page,
        "categories": ResourceCategory.objects.all(),
        "states": IndianState.objects.all(),
        "selected_category": category_id,
        "selected_state": state_id,
        "low_stock": low_stock,
        "q": q,
        "total_resources": total_resources,
        "low_stock_count": low_stock_count,
        "active_menu": "resources",
    })


@login_required
def resource_request_create_view(request):
    if request.method == "POST":
        resource_id = request.POST.get("resource_id")
        quantity = request.POST.get("quantity_requested", "0")
        purpose = request.POST.get("purpose", "")
        incident_id = request.POST.get("incident_id") or None

        try:
            resource = ReliefResource.objects.get(pk=resource_id)
            qty = int(quantity)
            if qty <= 0:
                raise ValueError("Quantity must be positive.")
            if qty > resource.quantity_free:
                messages.error(request, f"Only {resource.quantity_free} {resource.unit} available.")
                return redirect("disaster_management:resource_list")

            with transaction.atomic():
                ResourceRequest.objects.create(
                    resource=resource,
                    requested_by=request.user,
                    quantity_requested=qty,
                    purpose=purpose,
                    incident_id=incident_id,
                )
                resource.quantity_reserved = resource.quantity_reserved + qty
                resource.save(update_fields=["quantity_reserved", "updated_at"])

            messages.success(request, f"Resource request submitted for {qty} {resource.unit} of '{resource.name}'.")
        except ReliefResource.DoesNotExist:
            messages.error(request, "Resource not found.")
        except (ValueError, TypeError) as e:
            messages.error(request, str(e))

        return redirect("disaster_management:resource_list")

    resources = ReliefResource.objects.select_related("category").order_by("name")
    incidents = Incident.objects.filter(
        status__in=[Incident.Status.VERIFIED, Incident.Status.DISPATCHED, Incident.Status.ON_SCENE]
    ).order_by("-created_at")[:30]
    return render(request, "dashboard/resource_request_form.html", {
        "resources": resources,
        "incidents": incidents,
        "active_menu": "resources",
    })


@login_required
def resource_request_list_view(request):
    if _is_admin(request.user):
        qs = ResourceRequest.objects.select_related("resource", "requested_by", "incident").all()
    else:
        qs = ResourceRequest.objects.select_related("resource", "incident").filter(requested_by=request.user)

    status_filter = request.GET.get("status", "")
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs.order_by("-created_at"), 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "dashboard/resource_requests.html", {
        "page_obj": page,
        "status_choices": ResourceRequest.Status.choices,
        "selected_status": status_filter,
        "active_menu": "resources",
    })


@login_required
@require_POST
def resource_request_action_view(request, req_id):
    if not _is_admin(request.user):
        messages.error(request, "Forbidden.")
        return redirect("disaster_management:resource_requests")

    req = get_object_or_404(ResourceRequest, pk=req_id)
    action = request.POST.get("action")
    admin_note = request.POST.get("admin_note", "")

    with transaction.atomic():
        if action == "approve":
            qty_approved = int(request.POST.get("quantity_approved", req.quantity_requested))
            req.status = ResourceRequest.Status.APPROVED
            req.quantity_approved = qty_approved
            req.approved_by = request.user
            req.approved_at = timezone.now()
            req.admin_note = admin_note
            req.save()
            messages.success(request, f"Request #{req.id} approved.")

        elif action == "reject":
            # Release the reserved quantity
            resource = req.resource
            resource.quantity_reserved = max(0, resource.quantity_reserved - req.quantity_requested)
            resource.save(update_fields=["quantity_reserved", "updated_at"])
            req.status = ResourceRequest.Status.REJECTED
            req.admin_note = admin_note
            req.save()
            messages.success(request, f"Request #{req.id} rejected.")

        elif action == "dispatch":
            if req.status != ResourceRequest.Status.APPROVED:
                messages.error(request, "Can only dispatch approved requests.")
                return redirect("disaster_management:resource_requests")
            resource = req.resource
            if resource.quantity_available < req.quantity_approved:
                messages.error(request, "Insufficient stock to dispatch.")
                return redirect("disaster_management:resource_requests")
            resource.quantity_available -= req.quantity_approved
            resource.quantity_reserved = max(0, resource.quantity_reserved - req.quantity_requested)
            resource.save(update_fields=["quantity_available", "quantity_reserved", "updated_at"])
            req.status = ResourceRequest.Status.DISPATCHED
            req.dispatched_at = timezone.now()
            req.save()
            messages.success(request, f"Request #{req.id} dispatched.")

        elif action == "deliver":
            req.status = ResourceRequest.Status.DELIVERED
            req.delivered_at = timezone.now()
            req.save()
            messages.success(request, f"Request #{req.id} marked delivered.")

    return redirect("disaster_management:resource_requests")


# ---------------------------------------------------------------------------
# National Overview Dashboard
# ---------------------------------------------------------------------------

@login_required
def national_dashboard_view(request):
    if not _is_admin(request.user):
        messages.warning(request, "National dashboard is restricted to administrators.")
        return redirect("disaster_management:dashboard_home")

    # Active incidents by type
    type_breakdown = (
        Incident.objects.filter(
            status__in=[
                Incident.Status.PENDING, Incident.Status.VERIFIED,
                Incident.Status.DISPATCHED, Incident.Status.ON_SCENE,
                Incident.Status.ESCALATED,
            ]
        )
        .values("incident_type")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    # State-wise incident counts
    state_incidents = (
        Incident.objects.filter(state__isnull=False)
        .values("state__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:10]
    )

    # Risk summary
    very_high_zones = DisasterRiskZone.objects.filter(is_active=True, risk_level="very_high").count()
    high_zones = DisasterRiskZone.objects.filter(is_active=True, risk_level="high").count()

    # Active warnings
    active_warnings = EarlyWarning.objects.filter(is_active=True).order_by("-severity", "-issued_at")[:8]

    # Shelter stats
    total_capacity = Shelter.objects.aggregate(c=Sum("capacity"))["c"] or 0
    total_occupancy = Shelter.objects.aggregate(c=Sum("current_occupancy"))["c"] or 0

    # Resource low-stock alerts
    low_stock_resources = [r for r in ReliefResource.objects.select_related("category").all() if r.is_low_stock][:8]

    # Totals
    total_incidents = Incident.objects.count()
    active_incidents = Incident.objects.filter(
        status__in=[
            Incident.Status.PENDING, Incident.Status.VERIFIED,
            Incident.Status.DISPATCHED, Incident.Status.ON_SCENE,
            Incident.Status.ESCALATED,
        ]
    ).count()
    closed_incidents = Incident.objects.filter(status=Incident.Status.CLOSED).count()
    pending_requests = ResourceRequest.objects.filter(status=ResourceRequest.Status.PENDING).count()

    return render(request, "dashboard/national_dashboard.html", {
        "type_breakdown": list(type_breakdown),
        "state_incidents": list(state_incidents),
        "very_high_zones": very_high_zones,
        "high_zones": high_zones,
        "active_warnings": active_warnings,
        "total_capacity": total_capacity,
        "total_occupancy": total_occupancy,
        "low_stock_resources": low_stock_resources,
        "total_incidents": total_incidents,
        "active_incidents": active_incidents,
        "closed_incidents": closed_incidents,
        "pending_requests": pending_requests,
        "active_menu": "national_dashboard",
    })


# ---------------------------------------------------------------------------
# AJAX: Districts by state
# ---------------------------------------------------------------------------

@require_GET
def districts_by_state_view(request):
    state_id = request.GET.get("state_id")
    if not state_id:
        return JsonResponse({"districts": []})
    districts = list(
        IndianDistrict.objects.filter(state_id=state_id).values("id", "name").order_by("name")
    )
    return JsonResponse({"districts": districts})


# ---------------------------------------------------------------------------
# Location Subscriptions
# ---------------------------------------------------------------------------

@login_required
@require_POST
def subscribe_location_view(request):
    """Subscribe to location-based warning notifications."""
    state_id = request.POST.get('state_id') or None
    district_id = request.POST.get('district_id') or None
    notify_in_app = request.POST.get('notify_in_app') == 'on'
    notify_email = request.POST.get('notify_email') == 'on'

    if not state_id and not district_id:
        messages.error(request, 'Please select a state or district to subscribe.')
        return redirect('disaster_management:warning_list')

    from .models import LocationSubscription
    try:
        sub, created = LocationSubscription.objects.get_or_create(
            user=request.user,
            state_id=state_id,
            district_id=district_id,
            defaults={'notify_in_app': notify_in_app, 'notify_email': notify_email},
        )
        if created:
            messages.success(request, 'You are now subscribed to alerts for this location.')
        else:
            sub.notify_in_app = notify_in_app
            sub.notify_email = notify_email
            sub.save(update_fields=['notify_in_app', 'notify_email'])
            messages.info(request, 'Subscription preferences updated.')
    except Exception as e:
        messages.error(request, f'Could not save subscription: {e}')

    return redirect('disaster_management:warning_list')


@login_required
@require_POST
def unsubscribe_location_view(request, sub_id):
    from .models import LocationSubscription
    sub = get_object_or_404(LocationSubscription, pk=sub_id, user=request.user)
    sub.delete()
    messages.success(request, 'Unsubscribed from location alerts.')
    return redirect('disaster_management:my_subscriptions')


@login_required
def my_subscriptions_view(request):
    from .models import LocationSubscription
    subs = LocationSubscription.objects.filter(user=request.user).select_related('state', 'district')
    states = IndianState.objects.all()
    return render(request, 'dashboard/my_subscriptions.html', {
        'subscriptions': subs,
        'states': states,
        'active_menu': 'warnings',
    })


# ---------------------------------------------------------------------------
# Damage Assessments
# ---------------------------------------------------------------------------

@login_required
def damage_assessment_list_view(request):
    from .models import DamageAssessment
    if request.user.is_superuser:
        qs = DamageAssessment.objects.select_related('incident', 'assessed_by', 'state', 'district').all()
    else:
        qs = DamageAssessment.objects.select_related('incident', 'state', 'district').filter(assessed_by=request.user)
    
    status_filter = request.GET.get('status', '')
    if status_filter:
        qs = qs.filter(status=status_filter)
    
    paginator = Paginator(qs.order_by('-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))
    
    return render(request, 'dashboard/damage_assessment_list.html', {
        'page_obj': page,
        'status_choices': DamageAssessment.Status.choices,
        'selected_status': status_filter,
        'active_menu': 'damage_assessments',
    })


@login_required
def damage_assessment_create_view(request, incident_id):
    from .models import DamageAssessment
    incident = get_object_or_404(Incident, pk=incident_id)
    # Only responders assigned to incident or admins can submit
    if not request.user.is_superuser:
        from .models import IncidentAssignment
        has_assignment = IncidentAssignment.objects.filter(
            incident=incident, responder=request.user,
            status__in=['accepted', 'notified']
        ).exists()
        if not has_assignment:
            messages.error(request, 'Only assigned responders or admins can submit damage assessments.')
            return redirect('disaster_management:incident_detail', incident_id=incident.id)
    
    if request.method == 'POST':
        severity = request.POST.get('severity', 'moderate')
        homes_damaged = int(request.POST.get('homes_damaged', 0) or 0)
        homes_destroyed = int(request.POST.get('homes_destroyed', 0) or 0)
        roads_damaged_km = float(request.POST.get('roads_damaged_km', 0) or 0)
        crops_damaged_hectares = float(request.POST.get('crops_damaged_hectares', 0) or 0)
        public_infra_damaged = request.POST.get('public_infra_damaged', '')
        estimated_loss_lakhs = float(request.POST.get('estimated_loss_lakhs', 0) or 0)
        description = request.POST.get('description', '')
        state_id = request.POST.get('state_id') or None
        district_id = request.POST.get('district_id') or None
        
        assessment = DamageAssessment.objects.create(
            incident=incident,
            assessed_by=request.user,
            severity=severity,
            status=DamageAssessment.Status.SUBMITTED,
            homes_damaged=homes_damaged,
            homes_destroyed=homes_destroyed,
            roads_damaged_km=roads_damaged_km,
            crops_damaged_hectares=crops_damaged_hectares,
            public_infra_damaged=public_infra_damaged,
            estimated_loss_lakhs=estimated_loss_lakhs,
            description=description,
            state_id=state_id,
            district_id=district_id,
        )
        messages.success(request, f'Damage assessment #{assessment.id} submitted.')
        return redirect('disaster_management:incident_detail', incident_id=incident.id)
    
    return render(request, 'dashboard/damage_assessment_form.html', {
        'incident': incident,
        'states': IndianState.objects.all(),
        'severity_choices': DamageAssessment.Severity.choices,
        'active_menu': 'damage_assessments',
    })


@login_required
def damage_assessment_detail_view(request, assessment_id):
    from .models import DamageAssessment
    assessment = get_object_or_404(DamageAssessment, pk=assessment_id)
    return render(request, 'dashboard/damage_assessment_detail.html', {
        'assessment': assessment,
        'active_menu': 'damage_assessments',
    })


@login_required
@require_POST
def damage_assessment_verify_view(request, assessment_id):
    from .models import DamageAssessment
    if not request.user.is_superuser:
        messages.error(request, 'Only admins can verify damage assessments.')
        return redirect('disaster_management:damage_assessment_list')
    assessment = get_object_or_404(DamageAssessment, pk=assessment_id)
    action = request.POST.get('action')
    if action == 'verify':
        assessment.status = DamageAssessment.Status.VERIFIED
        assessment.verified_by = request.user
        assessment.verified_at = timezone.now()
        assessment.save()
        messages.success(request, 'Assessment verified.')
    elif action == 'reject':
        assessment.status = DamageAssessment.Status.REJECTED
        assessment.save()
        messages.success(request, 'Assessment rejected.')
    return redirect('disaster_management:damage_assessment_detail', assessment_id=assessment_id)


# ---------------------------------------------------------------------------
# Infrastructure Registry
# ---------------------------------------------------------------------------

@login_required
def infrastructure_list_view(request):
    from .models import InfrastructureAsset
    qs = InfrastructureAsset.objects.select_related('state', 'district').all()
    
    # Hide sensitive assets from non-admins
    if not request.user.is_superuser:
        qs = qs.filter(is_sensitive=False)
    
    category = request.GET.get('category', '')
    state_id = request.GET.get('state', '')
    q = request.GET.get('q', '')
    
    if category:
        qs = qs.filter(category=category)
    if state_id:
        qs = qs.filter(state_id=state_id)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(address__icontains=q))
    
    paginator = Paginator(qs.order_by('name'), 25)
    page = paginator.get_page(request.GET.get('page'))
    
    return render(request, 'dashboard/infrastructure_list.html', {
        'page_obj': page,
        'category_choices': InfrastructureAsset.Category.choices,
        'states': IndianState.objects.all(),
        'selected_category': category,
        'selected_state': state_id,
        'q': q,
        'total': qs.count(),
        'active_menu': 'infrastructure',
    })


@login_required
def infrastructure_create_view(request):
    from .models import InfrastructureAsset
    if not request.user.is_superuser:
        messages.error(request, 'Only admins can add infrastructure assets.')
        return redirect('disaster_management:infrastructure_list')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        category = request.POST.get('category', 'other')
        lat = request.POST.get('latitude', '')
        lon = request.POST.get('longitude', '')
        state_id = request.POST.get('state_id') or None
        district_id = request.POST.get('district_id') or None
        address = request.POST.get('address', '')
        capacity = int(request.POST.get('capacity', 0) or 0)
        contact_phone = request.POST.get('contact_phone', '')
        notes = request.POST.get('notes', '')
        is_sensitive = request.POST.get('is_sensitive') == 'on'
        
        if not name or not lat or not lon:
            messages.error(request, 'Name, latitude and longitude are required.')
        else:
            try:
                InfrastructureAsset.objects.create(
                    name=name, category=category,
                    latitude=float(lat), longitude=float(lon),
                    state_id=state_id, district_id=district_id,
                    address=address, capacity=capacity,
                    contact_phone=contact_phone, notes=notes,
                    is_sensitive=is_sensitive,
                )
                messages.success(request, f"Asset '{name}' added.")
                return redirect('disaster_management:infrastructure_list')
            except (ValueError, TypeError) as e:
                messages.error(request, f'Invalid data: {e}')
    
    return render(request, 'dashboard/infrastructure_form.html', {
        'states': IndianState.objects.all(),
        'category_choices': InfrastructureAsset.Category.choices,
        'active_menu': 'infrastructure',
        'form_title': 'Add Infrastructure Asset',
    })


@login_required
def infrastructure_map_data_view(request):
    """JSON for infrastructure layer on the India map."""
    from .models import InfrastructureAsset
    qs = InfrastructureAsset.objects.filter(is_operational=True)
    if not request.user.is_superuser:
        qs = qs.filter(is_sensitive=False)
    assets = list(qs.values('id', 'name', 'category', 'latitude', 'longitude', 'capacity'))
    return JsonResponse({'assets': [
        {**a, 'latitude': float(a['latitude']), 'longitude': float(a['longitude'])}
        for a in assets
    ]})
