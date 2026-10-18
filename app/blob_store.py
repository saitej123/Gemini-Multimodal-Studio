"""Slide frames, audio and the finished video, off the container disk.

Cloud Run wipes the filesystem when the instance restarts. Job metadata already
lives in Firestore; the binaries are too big for a Firestore document, so they
go in a private Cloud Storage bucket when ``GCS_BUCKET`` is set.

Local runs leave this off. Files stay in ``workspace/`` and ``/files`` reads
them from disk. On a hosted instance, ``/files`` still prefers the local copy
and downloads from the bucket only when the disk copy is gone.
"""
from __future__ import annotations

import mimetypes
import threading
from pathlib import Path

from .logging_setup import log

_LOG = log.bind(task="storage")
_CLIENT = None
_TRIED = False
_DISABLED = False
_LOCK = threading.Lock()

# What is worth keeping across a restart. Frame dumps (every seek PNG) are
# skipped — they are huge and only needed while this instance is assembling.
_KEEP = (
    "presentation.html",
    "final.mp4",
    "thumbnail.png",
    "thumbnail.jpg",
    "cover.png",
    "cover.jpg",
    "cover.jpeg",
    "cover.webp",
    "meta.json",
)


def bucket_name() -> str:
    from .firestore_sync import _cfg, hosted

    # 1. Explicit GCS_BUCKET or FIREBASE_STORAGE_BUCKET from env
    explicit = _cfg("GCS_BUCKET") or _cfg("FIREBASE_STORAGE_BUCKET")
    if explicit:
        return explicit

    # 2. On Cloud Run / hosted, auto-detect the project bucket
    if hosted():
        proj = _cfg("GOOGLE_CLOUD_PROJECT") or _cfg("PROJECT_ID") or _cfg("GCP_PROJECT_ID")
        if not proj:
            try:
                from google.cloud import storage

                c = storage.Client()
                proj = c.project
            except Exception:
                pass
        if proj:
            return f"{proj}-studio-media"
    return ""


def active() -> bool:
    return bool(bucket_name()) and not _DISABLED


def _client():
    global _CLIENT, _TRIED
    if _CLIENT is not None or _TRIED:
        return _CLIENT
    _TRIED = True
    name = bucket_name()
    if not name:
        return None
    try:
        from google.cloud import storage

        _CLIENT = storage.Client()
        return _CLIENT
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"Cloud Storage client unavailable ({e}). Files stay on local disk.")
        return None


def _safe_rel(rel: str) -> str | None:
    text = (rel or "").replace("\\", "/").lstrip("/")
    if not text or text.startswith("../") or "/../" in f"/{text}/":
        return None
    parts = Path(text).parts
    if ".." in parts or any(p.startswith("/") for p in parts):
        return None
    return text


def _blob_path(job_id: str, rel: str) -> str | None:
    rel_ok = _safe_rel(rel)
    if not rel_ok or not job_id:
        return None
    return f"jobs/{job_id}/{rel_ok}"


def _interesting(work: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if not work.is_dir():
        return found
    for name in _KEEP:
        p = work / name
        if p.is_file():
            found.append((name, p))
    for folder, pattern in (
        ("pages", "*"),
        ("audio", "*"),
        ("social", "**/*"),
    ):
        root = work / folder
        if not root.is_dir():
            continue
        for p in root.glob(pattern):
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".mp4"}:
                found.append((p.relative_to(work).as_posix(), p))
    shots = work / "shots"
    if shots.is_dir():
        for p in shots.glob("slide_*.png"):
            found.append((p.relative_to(work).as_posix(), p))
        for d in shots.glob("slide_*"):
            if not d.is_dir():
                continue
            for name in ("frame_00000.png", "settled.png", "keyframes.json"):
                p = d / name
                if p.is_file():
                    found.append((p.relative_to(work).as_posix(), p))
    return found


def _upload_one(bucket, job_id: str, rel: str, path: Path) -> None:
    key = _blob_path(job_id, rel)
    if not key or not path.is_file():
        return
    media, _ = mimetypes.guess_type(path.name)
    blob = bucket.blob(key)
    blob.cache_control = "private, max-age=3600"
    blob.upload_from_filename(str(path), content_type=media or "application/octet-stream")


def upload_one_now(job_id: str, rel: str, path: Path) -> bool:
    """Synchronously upload a single file (e.g. on-demand thumbnail, final video)."""
    if not active():
        return False
    client = _client()
    if client is None:
        return False
    name = bucket_name()
    try:
        bucket = client.bucket(name)
        _upload_one(bucket, job_id, rel, path)
        _LOG.info(f"job {job_id}: uploaded {rel} to gs://{name}")
        return True
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"job {job_id}: could not upload {rel} to gs://{name}: {e}")
        return False


def sync_workspace(job_id: str, work: Path) -> int:
    """Upload the durable files for one job. Returns how many objects were sent.

    No-op when ``GCS_BUCKET`` is unset (a normal local run). A missing bucket
    disables further attempts for this process so the pipeline is not blocked
    on a storage setup error.
    """
    global _DISABLED
    if not active():
        return 0
    client = _client()
    if client is None:
        return 0
    name = bucket_name()
    try:
        bucket = client.bucket(name)
        sent = 0
        for rel, path in _interesting(work):
            _upload_one(bucket, job_id, rel, path)
            sent += 1
        if sent:
            _LOG.info(f"job {job_id}: stored {sent} files in gs://{name}")
        return sent
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "404" in msg or "bucket" in msg or "not found" in msg or "403" in msg:
            _DISABLED = True
            _LOG.warning(
                f"Cloud Storage bucket {name} is not usable ({e}). "
                "Slides stay on this instance's disk until the bucket exists."
            )
        else:
            _LOG.warning(f"job {job_id}: storage upload failed: {e}")
        return 0


def sync_workspace_async(job_id: str, work: Path) -> None:
    """Upload without holding the pipeline. The thread is not a daemon so a
    finishing job still gets its files out before the process winds down."""
    if not active():
        return

    def _run() -> None:
        with _LOCK:
            sync_workspace(job_id, work)

    threading.Thread(target=_run, name="gcs-sync", daemon=False).start()


def fetch(job_id: str, rel: str, dest: Path) -> bool:
    """Download one object onto ``dest``. False when storage is off or missing."""
    if not active():
        return False
    client = _client()
    key = _blob_path(job_id, rel)
    if client is None or not key:
        return False
    try:
        blob = client.bucket(bucket_name()).blob(key)
        if not blob.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest))
        return dest.is_file()
    except Exception as e:  # noqa: BLE001
        _LOG.warning(f"job {job_id}: could not fetch {rel}: {e}")
        return False
