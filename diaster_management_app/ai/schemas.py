from dataclasses import dataclass


@dataclass
class TriageResult:
    incident_type: str
    severity: str
    confidence: float
    risk_score: float
