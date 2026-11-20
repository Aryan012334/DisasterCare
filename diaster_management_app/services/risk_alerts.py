from datetime import timedelta
from math import asin, cos, radians, sin, sqrt

from django.utils import timezone

from diaster_management_app.models import Incident


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def _clamp(value, low, high):
    return max(low, min(high, value))


def _severity_weight(severity):
    mapping = {
        Incident.Severity.LOW: 18,
        Incident.Severity.MEDIUM: 34,
        Incident.Severity.HIGH: 58,
        Incident.Severity.CRITICAL: 76,
    }
    return mapping.get(severity, 24)


def _risk_level(score):
    if score >= 75:
        return Incident.RiskLevel.SEVERE
    if score >= 55:
        return Incident.RiskLevel.HIGH
    if score >= 35:
        return Incident.RiskLevel.MODERATE
    return Incident.RiskLevel.LOW


def _forecast_advisory(incident, risk_level):
    if incident.incident_type == Incident.IncidentType.FIRE:
        if risk_level in {Incident.RiskLevel.HIGH, Incident.RiskLevel.SEVERE}:
            return "Smoke concentration likely to rise. Limit outdoor exposure and protect breathing."
        return "Fire risk remains watchful. Keep clear from heat/smoke zones."
    if incident.incident_type == Incident.IncidentType.FLOOD:
        return "Water level and contamination risk may rise. Avoid low roads and stream crossings."
    if incident.incident_type == Incident.IncidentType.EARTHQUAKE:
        return "Aftershock risk persists. Avoid unstable structures and damaged utility lines."
    if incident.incident_type == Incident.IncidentType.LANDSLIDE:
        return "Ground instability risk continues. Keep away from slopes and loose debris."
    if incident.incident_type == Incident.IncidentType.ROAD_ACCIDENT:
        return "Traffic and secondary collision risk may increase near the site."
    if incident.incident_type == Incident.IncidentType.MEDICAL:
        return "Medical load may increase around this zone. Keep access routes clear."
    return "Risk remains active in this zone. Follow official local safety instructions."


def generate_consequence_warnings(incident):
    warnings = []
    major_fire = (
        incident.incident_type == Incident.IncidentType.FIRE
        and (
            incident.severity in {Incident.Severity.HIGH, Incident.Severity.CRITICAL}
            or incident.people_affected >= 15
            or incident.affected_radius_km >= 2.0
        )
    )

    if major_fire:
        warnings.extend(
            [
                "Air pollution risk is high due to smoke and fine particles. Use masks and stay indoors when possible.",
                "Visibility may drop sharply around the incident corridor. Avoid non-essential travel.",
                "People with breathing illness, elderly persons, and children are at higher risk from exposure.",
                "Heat and ember spread can trigger secondary fires in nearby dry zones.",
            ]
        )
        return warnings

    if incident.incident_type == Incident.IncidentType.FIRE:
        warnings.extend(
            [
                "Smoke drift may affect nearby neighborhoods.",
                "Keep windows closed if smoke odor increases.",
                "Avoid standing downwind of the incident area.",
            ]
        )
    elif incident.incident_type == Incident.IncidentType.FLOOD:
        warnings.extend(
            [
                "Water contamination and water-borne infection risk may increase near flood pockets.",
                "Avoid electrical equipment in waterlogged buildings.",
                "Road washout risk may rise during continued rainfall.",
            ]
        )
    elif incident.incident_type == Incident.IncidentType.EARTHQUAKE:
        warnings.extend(
            [
                "Aftershock probability remains elevated for nearby structures.",
                "Damaged buildings, power lines, and gas lines can cause secondary hazards.",
            ]
        )
    elif incident.incident_type == Incident.IncidentType.LANDSLIDE:
        warnings.extend(
            [
                "Slope movement may continue even after the first slide.",
                "Loose debris can affect roads and rescue access routes.",
            ]
        )
    elif incident.incident_type == Incident.IncidentType.ROAD_ACCIDENT:
        warnings.extend(
            [
                "Traffic congestion may delay emergency movement in nearby lanes.",
                "Fuel leakage/fire risk can increase around collision points.",
            ]
        )
    elif incident.incident_type == Incident.IncidentType.MEDICAL:
        warnings.extend(
            [
                "Nearby healthcare resources may face sudden load increase.",
                "Keep emergency access corridors clear for ambulances.",
            ]
        )
    else:
        warnings.extend(
            [
                "Potential secondary hazards may develop near the affected zone.",
                "Please monitor official updates and avoid blocked routes.",
            ]
        )
    return warnings


def build_time_series_predictions(incident):
    now = timezone.now()
    lookback_30_days = now - timedelta(days=30)
    history = Incident.objects.filter(
        incident_type=incident.incident_type,
        created_at__gte=lookback_30_days,
    ).exclude(id=incident.id)

    nearby_history = []
    for row in history.only("id", "latitude", "longitude", "created_at"):
        distance = _haversine_km(
            float(incident.latitude),
            float(incident.longitude),
            float(row.latitude),
            float(row.longitude),
        )
        if distance <= 25:
            nearby_history.append(row)

    counts_by_hour = []
    for offset in range(6, 0, -1):
        window_start = now - timedelta(hours=offset)
        window_end = now - timedelta(hours=offset - 1)
        count = 0
        for row in nearby_history:
            if window_start <= row.created_at < window_end:
                count += 1
        counts_by_hour.append(count)

    trend = 0.0
    if counts_by_hour:
        trend = (counts_by_hour[-1] - counts_by_hour[0]) / max(1, len(counts_by_hour) - 1)

    density_24h = 0
    window_24h = now - timedelta(hours=24)
    for row in nearby_history:
        if row.created_at >= window_24h:
            density_24h += 1

    base_score = float(_severity_weight(incident.severity))
    if incident.report_mode == Incident.ReportMode.EMERGENCY:
        base_score += 12
    base_score += min(float(incident.people_affected), 120) * 0.2
    base_score += min(float(incident.affected_radius_km), 15) * 1.4

    forecast_hours = [1, 3, 6, 12, 24]
    predictions = []
    for hours in forecast_hours:
        trend_component = trend * (hours * 4.5)
        density_component = min(density_24h, 40) * 0.6
        decay = max(0.6, 1.0 - (hours / 36.0))
        score = _clamp((base_score + density_component + trend_component) * decay, 5, 98)
        level = _risk_level(score)
        predictions.append(
            {
                "horizon_hours": hours,
                "forecast_for": now + timedelta(hours=hours),
                "risk_score": round(score, 1),
                "risk_level": level,
                "advisory": _forecast_advisory(incident, level),
            }
        )
    return predictions
