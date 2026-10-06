"""
Re-host import thumbnails to permanent Firebase Storage.

The TikTok / Instagram / web thumbnails we extract are short-lived CDN links
(Instagram's are signed and expire within minutes). If the client stored those
directly, the recipe image would break once the link expired — the recurring
"recipe image doesn't load" bug. So the worker downloads the FRESH thumbnail at
import time (while the URL is still valid) and uploads it to Firebase Storage,
returning a permanent, tokenised download URL — the same shape the Firebase SDK's
`downloadURL()` produces — which never expires.

Reuses the FIREBASE_SERVICE_ACCOUNT credential the worker already has. The target
bucket is STORAGE_BUCKET if set, else the project's default ({project}.appspot.com).
"""
import os
import json
import uuid
import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Browser-like UA — some CDNs (TikTok / Instagram) 403 non-browser requests.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
_DOWNLOAD_TIMEOUT = 15
_MAX_ATTEMPTS = 3

_storage_client = None


def _service_account_key() -> dict:
    sa = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
    if not sa:
        raise EnvironmentError("FIREBASE_SERVICE_ACCOUNT is not set")
    return json.loads(sa)


def _bucket_name() -> str:
    # Explicit override wins; otherwise the project's default Storage bucket.
    return os.environ.get("STORAGE_BUCKET") or f'{_service_account_key()["project_id"]}.appspot.com'


def _get_storage_client():
    global _storage_client
    if _storage_client is None:
        from google.cloud import storage
        from google.oauth2 import service_account
        key = _service_account_key()
        creds = service_account.Credentials.from_service_account_info(key)
        _storage_client = storage.Client(project=key["project_id"], credentials=creds)
    return _storage_client


def rehost_image(url: str, request_id: str) -> str | None:
    """Download `url` (the fresh thumbnail) and upload it to Firebase Storage under
    recipe-imports/{request_id}/cover.<ext>, returning a permanent tokenised
    download URL. Returns None on any failure (invalid URL, non-image response,
    upload error) — the caller decides whether that's fatal (media / photo-bearing
    web imports) or fine (text / photo-less web imports)."""
    if not url:
        return None

    import requests  # lazy: only the worker path needs it at call time

    data = None
    content_type = "image/jpeg"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=_DOWNLOAD_TIMEOUT)
            if resp.status_code != 200:
                logger.warning(f"Thumbnail fetch status {resp.status_code} (attempt {attempt}) for {url}")
                continue
            ctype = resp.headers.get("Content-Type", "image/jpeg")
            if not ctype.startswith("image/"):
                # A non-image body (e.g. an expired-link error page) won't fix
                # itself on retry — bail now.
                logger.warning(f"Thumbnail fetch wasn't an image ({ctype}) for {url}")
                return None
            data = resp.content
            content_type = ctype
            break
        except Exception as e:
            logger.warning(f"Thumbnail fetch error (attempt {attempt}) for {url}: {e}")
    if data is None:
        return None

    try:
        ext = "png" if "png" in content_type else "jpg"
        path = f"recipe-imports/{request_id}/cover.{ext}"
        token = str(uuid.uuid4())
        bucket_name = _bucket_name()
        blob = _get_storage_client().bucket(bucket_name).blob(path)
        # firebaseStorageDownloadTokens makes the tokenised URL below resolve
        # publicly — the same mechanism the Firebase SDK's downloadURL() uses.
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        blob.upload_from_string(data, content_type=content_type)
        encoded = quote(path, safe="")
        return (
            f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}"
            f"/o/{encoded}?alt=media&token={token}"
        )
    except Exception as e:
        logger.error(f"Thumbnail upload to Storage failed for {url}: {e}")
        return None
