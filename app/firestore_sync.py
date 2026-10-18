"""Firebase & Google Cloud Firestore real-time state synchronization.

Synchronizes Multimodal Studio job snapshots, session history, and generation
audits directly to the ``studio_jobs`` collection in Cloud Firestore.

- Local runs: Mirror snapshots to Firestore when configured, while keeping SQLite for local cache.
- Cloud Run / Firebase: Full persistence across ephemeral container instances and serverless cold starts.
"""
from __future__ import annotations

import os
from typing import Any

from .config import get_settings
from .logging_setup import log

_LOG = log.bind(task="firestore")
_CLIENT = None
_READY = False
_SYNCED_COUNT = 0


def enabled() -> bool:
    """Return True if Firestore integration is enabled."""
    val = os.environ.get("FIRESTORE_ENABLED", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    # Auto-enable if running on Cloud Run or if GCP Project / Firebase config is set
    if os.environ.get("K_SERVICE") or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID"):
        return True
    return bool(getattr(get_settings(), "firestore_enabled", True))


def _collection_name() -> str:
    return os.environ.get("FIRESTORE_COLLECTION", "studio_jobs").strip() or "studio_jobs"


def _client():
    global _CLIENT, _READY
    if _CLIENT is not None:
        return _CLIENT
    if _READY:
        return None
    _READY = True
    if not enabled():
        return None

    # Try initializing google-cloud-firestore
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID") or None
    try:
        from google.cloud import firestore
        _CLIENT = firestore.Client(project=project)
        _LOG.info(f"Connected to Cloud Firestore (project={_CLIENT.project})")
        return _CLIENT
    except ImportError:
        _LOG.warning("google-cloud-firestore is not installed; attempting firebase-admin fallback")
    except Exception as e:
        _LOG.warning(f"google.cloud.firestore client init failed: {e}; trying firebase-admin")

    # Fallback to firebase_admin
    try:
        import firebase_admin
        from firebase_admin import firestore as fb_firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _CLIENT = fb_firestore.client()
        _LOG.info("Connected to Firestore via firebase_admin")
        return _CLIENT
    except Exception as e:
        _LOG.warning(f"Firestore client unavailable ({e}). Continuing with local SQLite persistence.")
        _CLIENT = None

    return _CLIENT


def mirror_job(snapshot: dict) -> bool:
    """Persist/merge a job snapshot into the Firestore collection."""
    global _SYNCED_COUNT
    if not enabled():
        return False
    client = _client()
    if client is None:
        return False
    job_id = str(snapshot.get("id") or "").strip()
    if not job_id:
        return False

    doc = {
        "id": job_id,
        "filename": snapshot.get("filename") or "",
        "status": snapshot.get("status") or "",
        "error": snapshot.get("error") or "",
        "user_id": snapshot.get("user_id") or "",
        "stages": snapshot.get("stages") or [],
        "artifacts": snapshot.get("artifacts") or {},
        "options": snapshot.get("options") or {},
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at") or snapshot.get("created_at"),
    }
    try:
        col = _collection_name()
        client.collection(col).document(job_id).set(doc, merge=True)
        _SYNCED_COUNT += 1
        return True
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"Firestore sync failed for job {job_id}: {e}")
        return False


def get_job(job_id: str) -> dict[str, Any] | None:
    """Retrieve a single job from Firestore by ID."""
    if not enabled():
        return None
    client = _client()
    if client is None:
        return None
    try:
        doc_ref = client.collection(_collection_name()).document(job_id)
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict()
    except Exception as e:
        _LOG.warning(f"Failed to fetch job {job_id} from Firestore: {e}")
    return None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """List recent jobs from Firestore."""
    if not enabled():
        return []
    client = _client()
    if client is None:
        return []
    try:
        col = client.collection(_collection_name())
        query = col.order_by("updated_at", direction="DESCENDING").limit(limit)
        return [doc.to_dict() for doc in query.stream()]
    except Exception as e:
        _LOG.warning(f"Failed to list jobs from Firestore: {e}")
        return []


def status() -> dict[str, Any]:
    """Diagnostic status payload for API and frontend display."""
    is_en = enabled()
    client = _client()
    is_conn = client is not None
    proj = getattr(client, "project", None) or os.environ.get("GOOGLE_CLOUD_PROJECT") or "auto"
    return {
        "enabled": is_en,
        "connected": is_conn,
        "project": str(proj) if is_conn else None,
        "collection": _collection_name(),
        "synced_jobs": _SYNCED_COUNT,
        "storage": "Firestore + SQLite WAL",
    }
