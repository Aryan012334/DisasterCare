"""
Background tasks for AI triage, geo-alert dispatch, and notifications.
Supports both direct synchronous execution and threaded asynchronous execution.
"""
import logging
import threading
from diaster_management_app.ai.triage import run_incident_triage

logger = logging.getLogger(__name__)


def process_incident_triage_task(incident_id: int):
    """
    Executes AI triage for a specific incident and updates its confidence,
    risk score, and optionally adjusts severity and type.
    """
    from diaster_management_app.models import Incident

    try:
        incident = Incident.objects.get(id=incident_id)
        result = run_incident_triage(incident.description)

        incident.ai_confidence = result.confidence
        incident.ai_risk_score = result.risk_score

        if incident.incident_type == Incident.IncidentType.OTHER and result.incident_type in dict(Incident.IncidentType.choices):
            incident.incident_type = result.incident_type

        if result.severity in dict(Incident.Severity.choices):
            if result.severity in {Incident.Severity.HIGH, Incident.Severity.CRITICAL} or incident.severity == Incident.Severity.LOW:
                incident.severity = result.severity

        incident.save(update_fields=["ai_confidence", "ai_risk_score", "incident_type", "severity"])
        logger.info(f"AI Triage completed for Incident #{incident_id}: {result.incident_type} ({result.severity})")
        return {"incident_id": incident_id, "success": True, "result": result.__dict__}
    except Incident.DoesNotExist:
        logger.warning(f"Incident #{incident_id} not found for triage.")
        return {"incident_id": incident_id, "success": False, "error": "Not found"}
    except Exception as e:
        logger.error(f"Error triaging incident #{incident_id}: {e}", exc_info=True)
        return {"incident_id": incident_id, "success": False, "error": str(e)}


def enqueue_incident_triage(incident_id: int, async_mode: bool = True):
    """
    Dispatches incident triage in a background worker thread (or synchronously if async_mode=False).
    """
    if async_mode:
        thread = threading.Thread(
            target=process_incident_triage_task,
            args=(incident_id,),
            daemon=True,
            name=f"TriageWorker-{incident_id}"
        )
        thread.start()
        return {"incident_id": incident_id, "queued": True, "mode": "async_thread"}
    else:
        return process_incident_triage_task(incident_id)
