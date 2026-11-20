from diaster_management_app.ai.triage import run_incident_triage


def build_incident_payload(description: str) -> dict:
    """
    Build a normalized payload for Incident creation.
    """
    triage = run_incident_triage(description)
    return {
        "description": description,
        "incident_type": triage.incident_type,
        "severity": triage.severity,
        "ai_confidence": triage.confidence,
        "ai_risk_score": triage.risk_score,
    }
