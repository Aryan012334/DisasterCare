from .schemas import TriageResult


def run_incident_triage(text: str) -> TriageResult:
    """
    AI triage stub.
    Replace with model/provider call and confidence calibration.
    """
    normalized = (text or "").lower()
    if "fire" in normalized:
        return TriageResult("fire", "high", 0.82, 0.11)
    if "flood" in normalized:
        return TriageResult("flood", "medium", 0.79, 0.09)
    return TriageResult("unknown", "low", 0.55, 0.15)
