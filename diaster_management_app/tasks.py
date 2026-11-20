"""
Background tasks (Celery/RQ) for AI triage, geo-alert dispatch, and notifications.
"""


def enqueue_incident_triage(incident_id):
    """
    Stub task function for asynchronous AI triage.
    """
    return {"incident_id": incident_id, "queued": True}

