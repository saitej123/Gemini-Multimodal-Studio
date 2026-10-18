"""Firebase & Google Cloud Firestore real-time state synchronization.

Synchronizes Multimodal Studio job snapshots, session history, and generation
audits directly to the ``studio_jobs`` collection in Cloud Firestore.

- Local runs: Firestore when credentials are configured, otherwise plain SQLite on disk.
- Cloud Run / Firebase: Firestore is the source of truth (containers are ephemeral).

Besides job snapshots this module also stores two more collections, so a hosted
instance keeps them across restarts:

  * ``studio_usage``        one document per LLM / TTS call (the admin cost report)
  * ``studio_agent_memory`` the agent harness' long-term memory
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from .config import get_settings
from .logging_setup import log

_LOG = log.bind(task="firestore")
_CLIENT = None
_READY = False


def _cfg(name: str, default: str = "") -> str:
    """Env var first, then the same key from ``.env`` (via Settings)."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        got = getattr(get_settings(), name.lower(), "")
    except Exception:  # noqa: BLE001
        got = ""
    return str(got).strip() if got not in (None, "") else default
_SYNCED_COUNT = 0


def enabled() -> bool:
    """Return True if Firestore integration is enabled."""
    val = os.environ.get("FIRESTORE_ENABLED", "").strip().lower()
    if val:
        return val in {"1", "true", "yes", "on"}
    if os.environ.get("K_SERVICE"):
        return True  # Cloud Run: Firestore is how state survives restarts
    return bool(getattr(get_settings(), "firestore_enabled", True))


def _collection_name() -> str:
    return _cfg("FIRESTORE_COLLECTION", "studio_jobs")


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
    project = _cfg("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID") or None
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


def _clean(value: Any, depth: int = 0, _seen: set | None = None) -> Any:
    """Plain, acyclic, depth-limited copy that Firestore can always encode."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:20000]
    if depth >= 5:
        return None
    seen = _seen if _seen is not None else set()
    if isinstance(value, (dict, list, tuple)):
        oid = id(value)
        if oid in seen:
            return None
        seen = seen | {oid}
        if isinstance(value, dict):
            return {str(k)[:100]: _clean(v, depth + 1, seen) for k, v in list(value.items())[:80]}
        return [_clean(v, depth + 1, seen) for v in list(value)[:80]]
    return str(value)[:500]


_PENDING: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()
_WAKE = threading.Event()
_WORKER: threading.Thread | None = None


def _build_doc(snapshot: dict) -> dict | None:
    """Acyclic, size-limited Firestore document from a job snapshot."""
    job_id = str(snapshot.get("id") or "").strip()
    if not job_id:
        return None
    options = snapshot.get("options") or {}
    keep_opts = {
        k: v for k, v in list(options.items())
        if k not in {"agent_receipt", "work_dir"} and isinstance(v, (str, int, float, bool, type(None)))
    }
    content = snapshot.get("content") or {}
    return {
        "id": job_id,
        "filename": snapshot.get("filename") or "",
        "status": snapshot.get("status") or "",
        "error": snapshot.get("error") or "",
        "user_id": snapshot.get("user_id") or "",
        "title": str(content.get("slide_title") or "")[:200],
        "slide_count": len(content.get("slides") or []),
        "stages": _clean(snapshot.get("stages") or []),
        "artifacts": _clean(snapshot.get("artifacts") or {}),
        "options": _clean(keep_opts),
        "created_at": snapshot.get("created_at"),
        "updated_at": time.time(),
    }


def _send(doc: dict) -> bool:
    global _SYNCED_COUNT
    client = _client()
    if client is None:
        return False
    try:
        client.collection(_collection_name()).document(doc["id"]).set(doc, merge=True)
        _SYNCED_COUNT += 1
        return True
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"Firestore sync failed for job {doc['id']}: {e}")
        return False


def _worker_loop() -> None:
    while True:
        _WAKE.wait()
        time.sleep(0.8)  # coalesce bursts of updates
        _WAKE.clear()
        with _PENDING_LOCK:
            batch = dict(_PENDING)
            _PENDING.clear()
        for doc in batch.values():
            _send(doc)


def queue_mirror(snapshot: dict) -> None:
    """Non-blocking: keep the latest doc per job and sync it in the background."""
    global _WORKER
    if not enabled():
        return
    try:
        doc = _build_doc(snapshot)
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"Firestore snapshot skipped: {e}")
        return
    if not doc:
        return
    with _PENDING_LOCK:
        _PENDING[doc["id"]] = doc
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker_loop, name="firestore-sync", daemon=True)
            _WORKER.start()
    _WAKE.set()


def mirror_job(snapshot: dict) -> bool:
    """Synchronous variant (kept for scripts/tests)."""
    if not enabled():
        return False
    doc = _build_doc(snapshot)
    return bool(doc) and _send(doc)


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


# --------------------------------------------------------- where data lives --
def hosted() -> bool:
    """True when running on Cloud Run (or forced with ``STUDIO_HOSTED=1``)."""
    if os.environ.get("STUDIO_HOSTED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(os.environ.get("K_SERVICE"))


def connected() -> bool:
    return enabled() and _client() is not None


def store_for(kind: str) -> str:
    """``"firestore"`` or ``"sqlite"`` for ``kind`` in ``{"costs", "memory"}``.

    ``COST_STORE`` / ``MEMORY_STORE`` can be ``auto`` (default), ``sqlite`` or
    ``firestore``. Auto means Firestore when a client is connected (always the case
    on Cloud Run), else SQLite, so a laptop with no credentials just works.
    """
    name = {"costs": "COST_STORE", "memory": "MEMORY_STORE"}.get(kind, "")
    pref = (_cfg(name) if name else "").lower() or "auto"
    if pref == "sqlite":
        return "sqlite"
    return "firestore" if connected() else "sqlite"


def usage_collection() -> str:
    return _cfg("FIRESTORE_USAGE_COLLECTION", "studio_usage")


def memory_collection() -> str:
    return _cfg("FIRESTORE_MEMORY_COLLECTION", "studio_agent_memory")


_USAGE_Q: list[dict] = []
_USAGE_LOCK = threading.Lock()
_USAGE_WAKE = threading.Event()
_USAGE_WORKER: threading.Thread | None = None
_USAGE_SENT = 0
_USAGE_ERROR = ""


def _flush_usage() -> int:
    """Write queued usage events in batches. Returns how many were written."""
    global _USAGE_SENT, _USAGE_ERROR
    with _USAGE_LOCK:
        batch_docs = list(_USAGE_Q)
        _USAGE_Q.clear()
    if not batch_docs:
        return 0
    client = _client()
    if client is None:
        return 0
    sent = 0
    col = client.collection(usage_collection())
    try:
        for i in range(0, len(batch_docs), 400):
            chunk = batch_docs[i:i + 400]
            b = client.batch()
            for d in chunk:
                b.set(col.document(d["id"]), d)
            b.commit()
            sent += len(chunk)
        _USAGE_ERROR = ""
    except Exception as e:  # noqa: BLE001
        _USAGE_ERROR = str(e)[:200]
        _LOG.warning(f"usage sync failed ({e}); {len(batch_docs) - sent} event(s) kept for retry")
        with _USAGE_LOCK:
            _USAGE_Q[:0] = batch_docs[sent:]
    _USAGE_SENT += sent
    return sent


def _usage_loop() -> None:
    while True:
        _USAGE_WAKE.wait()
        time.sleep(1.0)  # coalesce bursts
        _USAGE_WAKE.clear()
        try:
            _flush_usage()
        except Exception as e:  # noqa: BLE001
            _LOG.warning(f"usage worker error: {e}")


def queue_usage(doc: dict) -> None:
    """Non-blocking: queue one usage event for Firestore."""
    global _USAGE_WORKER
    if not connected():
        return
    with _USAGE_LOCK:
        _USAGE_Q.append(doc)
        if _USAGE_WORKER is None or not _USAGE_WORKER.is_alive():
            _USAGE_WORKER = threading.Thread(target=_usage_loop, name="firestore-usage", daemon=True)
            _USAGE_WORKER.start()
    _USAGE_WAKE.set()


def flush_usage() -> int:
    """Push pending usage events now (called before a report is read)."""
    try:
        return _flush_usage()
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"usage flush failed: {e}")
        return 0


def query_since(collection: str, field: str, since: float, limit: int = 50000) -> list[dict]:
    """Documents with ``field >= since``. Raises on failure so callers can fall back."""
    client = _client()
    if client is None:
        raise RuntimeError("Firestore not connected")
    col = client.collection(collection)
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = col.where(filter=FieldFilter(field, ">=", since))
    except Exception:  # noqa: BLE001 - older client without FieldFilter
        q = col.where(field, ">=", since)
    return [d.to_dict() for d in q.limit(limit).stream()]


def put_doc(collection: str, doc_id: str, data: dict) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        client.collection(collection).document(doc_id).set(data, merge=True)
        return True
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"Firestore write {collection}/{doc_id} failed: {e}")
        return False


def fetch_all(collection: str, limit: int = 1000) -> list[dict]:
    client = _client()
    if client is None:
        return []
    try:
        return [d.to_dict() for d in client.collection(collection).limit(limit).stream()]
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"Firestore read {collection} failed: {e}")
        return []



def status() -> dict[str, Any]:
    """Diagnostic status payload for the API and the admin UI."""
    is_en = enabled()
    client = _client() if is_en else None
    is_conn = client is not None
    proj = getattr(client, "project", None) or _cfg("GOOGLE_CLOUD_PROJECT") or None
    costs_store = store_for("costs")
    memory_store = store_for("memory")
    is_hosted = hosted()
    warning = ""
    if is_hosted and not is_conn:
        warning = ("Hosted without a Firestore connection: usage, memory and history are "
                   "lost whenever the container restarts. Check the service account role.")
    elif _USAGE_ERROR:
        warning = f"Last usage sync failed: {_USAGE_ERROR}"
    return {
        "enabled": is_en,
        "connected": is_conn,
        "hosted": is_hosted,
        "mode": "hosted" if is_hosted else "local",
        "project": str(proj) if proj else None,
        "collection": _collection_name(),
        "usage_collection": usage_collection(),
        "memory_collection": memory_collection(),
        "synced_jobs": _SYNCED_COUNT,
        "synced_usage": _USAGE_SENT,
        "pending_usage": len(_USAGE_Q),
        "stores": {"jobs": "firestore" if is_conn else "sqlite", "costs": costs_store, "memory": memory_store},
        "storage": ("Firestore (cloud) + SQLite cache" if is_conn else "SQLite on this machine"),
        "warning": warning,
    }
