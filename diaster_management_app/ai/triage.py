import re
from .schemas import TriageResult

# Keyword mappings for IncidentType
TYPE_PATTERNS = {
    "fire": [
        r"\bfire\b", r"\bflames?\b", r"\bblaze\b", r"\bburning\b", r"\bsmoke\b",
        r"\barson\b", r"\binferno\b", r"\bshort\s*circuit\s*fire\b", r"\bgas\s*cylinder\s*blast\b"
    ],
    "forest_fire": [
        r"\bforest\s*fire\b", r"\bwild\s*fire\b", r"\bbush\s*fire\b", r"\bjungle\s*fire\b"
    ],
    "flood": [
        r"\bflood(ing|s)?\b", r"\bwaterlogg(ed|ing)\b", r"\binundat(ion|ed)\b",
        r"\boverflow(ing)?\b", r"\bsubmerg(ed|ing)\b", r"\bflash\s*flood\b",
        r"\bdam\s*(burst|breach|overflow)\b", r"\bdeluge\b", r"\bwater\s*level\s*ris(ing|en)\b"
    ],
    "earthquake": [
        r"\bearthquake\b", r"\btremor(s)?\b", r"\bseismic\b", r"\baftershock(s)?\b",
        r"\bquake\b", r"\brichter\b"
    ],
    "cyclone": [
        r"\bcyclone\b", r"\bstorm\s*surge\b", r"\bhurricane\b", r"\btyphoon\b",
        r"\bgale\b", r"\bsevere\s*cyclonic\b", r"\blandfall\b", r"\btwister\b", r"\btornado\b"
    ],
    "landslide": [
        r"\blandslide(s)?\b", r"\bmudslide(s)?\b", r"\brockfall(s)?\b",
        r"\bslope\s*collapse\b", r"\bdebris\s*flow\b", r"\bmudflow\b"
    ],
    "drought": [
        r"\bdrought\b", r"\bwater\s*scarcity\b", r"\bcrop\s*failure\b",
        r"\bacute\s*water\s*shortage\b", r"\bfamine\b"
    ],
    "heatwave": [
        r"\bheat\s*wave\b", r"\bsunstroke\b", r"\bheatstroke\b", r"\bextreme\s*temperature\b",
        r"\bheat\s*exhaustion\b"
    ],
    "tsunami": [
        r"\btsunami\b", r"\btidal\s*wave\b", r"\bsea\s*surge\b"
    ],
    "road_accident": [
        r"\b(road\s*)?accident(s)?\b", r"\bcrash(ed|es)?\b", r"\bcollision(s)?\b",
        r"\bvehic(le|ular)\b", r"\btruck\s*accident\b", r"\bbus\s*(crash|overturned|accident)\b",
        r"\bpile\s*up\b", r"\bhit\s*and\s*run\b", r"\btrain\s*(derailment|crash|accident)\b"
    ],
    "medical": [
        r"\bmedical\s*emergency\b", r"\bheart\s*attack\b", r"\bcardiac\b", r"\bstroke\b",
        r"\bpoison(ing|ed)?\b", r"\bepidemic\b", r"\boutbreak\b", r"\bunconscious\b",
        r"\bambulance\b", r"\bsevere\s*bleeding\b", r"\basphyxiat(ion|ed)\b"
    ],
}

# Severity booster keywords
CRITICAL_TRIGGERS = [
    r"\bmass\s*casualt(y|ies)\b", r"\bfatalit(y|ies)\b", r"\bmultiple\s*deaths?\b",
    r"\bdozens\s*dead\b", r"\bhundreds\s*(trapped|stranded|dead|injured)\b",
    r"\bexplosion\b", r"\bblast\b", r"\bcollapse(d)?\s*building\b", r"\bevacuat(ion|e)\b",
    r"\bout\s*of\s*control\b", r"\bcatastroph(e|ic)\b", r"\blife\s*threatening\b",
    r"\bintensive\s*care\b", r"\bcritical\s*condition\b"
]

HIGH_TRIGGERS = [
    r"\binjur(ed|ies)\b", r"\btrapped\b", r"\bstranded\b", r"\bheavy\s*damage\b",
    r"\bhospital(ized|isation)?\b", r"\bsevere\b", r"\bspreading\b", r"\burgent\b",
    r"\brescue\s*needed\b", r"\bcasualt(y|ies)\b", r"\bmajor\b"
]

MEDIUM_TRIGGERS = [
    r"\bmoderate\b", r"\bpartial\s*damage\b", r"\bdelay\b", r"\bwater\s*logging\b",
    r"\btraffic\s*jam\b", r"\bblocked\b", r"\bminor\s*injur(y|ies)\b"
]


def run_incident_triage(text: str) -> TriageResult:
    """
    NLP & Heuristic AI triage engine.
    Analyzes text to classify disaster type, calculate severity, confidence, and calibrated risk score.
    """
    normalized = (text or "").lower().strip()
    if not normalized:
        return TriageResult(
            incident_type="other",
            severity="low",
            confidence=0.50,
            risk_score=20.0,
        )

    # 1. Classify incident type by pattern frequency and weighting
    type_scores = {}
    for itype, patterns in TYPE_PATTERNS.items():
        score = 0
        for pattern in patterns:
            matches = len(re.findall(pattern, normalized))
            if matches:
                # Specificity bonus
                weight = 2 if len(pattern) > 8 else 1
                score += matches * weight
        if score > 0:
            type_scores[itype] = score

    # Forest fire check over generic fire if forest/wild keywords exist
    if "forest_fire" in type_scores:
        incident_type = "forest_fire"
    elif type_scores:
        incident_type = max(type_scores.items(), key=lambda kv: kv[1])[0]
    else:
        incident_type = "other"

    # 2. Determine severity level
    critical_matches = sum(len(re.findall(p, normalized)) for p in CRITICAL_TRIGGERS)
    high_matches = sum(len(re.findall(p, normalized)) for p in HIGH_TRIGGERS)
    medium_matches = sum(len(re.findall(p, normalized)) for p in MEDIUM_TRIGGERS)

    if critical_matches > 0:
        severity = "critical"
        base_risk = 85.0 + min(critical_matches * 3.0, 10.0)
    elif high_matches > 0 or incident_type in {"tsunami", "earthquake"}:
        severity = "high"
        base_risk = 65.0 + min(high_matches * 4.0, 15.0)
    elif medium_matches > 0 or incident_type in {"fire", "flood", "cyclone", "landslide", "road_accident", "medical"}:
        severity = "medium"
        base_risk = 40.0 + min(medium_matches * 3.0, 15.0)
    else:
        severity = "low"
        base_risk = 20.0

    # 3. Calculate calibrated confidence (0.60 to 0.96)
    word_count = len(normalized.split())
    confidence = 0.65
    if incident_type != "other":
        confidence += 0.15
    if word_count >= 10:
        confidence += 0.08
    elif word_count >= 5:
        confidence += 0.04
    if critical_matches + high_matches + medium_matches > 0:
        confidence += 0.06

    confidence = min(0.96, round(confidence, 2))
    risk_score = min(99.0, max(5.0, round(base_risk, 1)))

    return TriageResult(
        incident_type=incident_type,
        severity=severity,
        confidence=confidence,
        risk_score=risk_score,
    )
