"""
Recipe-import producer Lambda.

Sits behind API Gateway (with the Firebase JWT authorizer in front) and does the
fast, synchronous part of an import: authenticate, validate, enqueue, return 202.
The heavy work (media download, transcription, OpenAI extraction) happens in the
worker Lambda that consumes the SQS queue — a recipe import runs far longer than
API Gateway's 29s integration timeout, so it must be async.

The user id is taken ONLY from the authorizer context (the verified Firebase
token), never from the request body — the client can no longer impersonate
another user by supplying a different userId.
"""
import os
import re
import json
import time
import logging
from datetime import datetime, timezone

import boto3

from ssrf import assert_safe_import, UnsafeURLError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

QUEUE_URL = os.environ.get("RECIPE_QUEUE_URL")

# Per-user import limits (cost control / abuse protection). Overridable via env.
QUOTA_TABLE = os.environ.get("QUOTA_TABLE_NAME")
DAILY_LIMIT = int(os.environ.get("DAILY_IMPORT_LIMIT", "30"))
MONTHLY_LIMIT = int(os.environ.get("MONTHLY_IMPORT_LIMIT", "150"))

_sqs = None
_ddb = None

# Guardrails on the single free-text field we accept. An import is only ever a
# pasted link or a short block of recipe text — anything larger is junk/abuse and
# is rejected before it can cost us a worker invocation. Sits just above the
# client-side 2,000-char cap, leaving margin for encoding differences.
MAX_INPUT_CHARS = 2500
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class QuotaExceeded(Exception):
    """Raised when a user is over their daily or monthly import limit."""
    def __init__(self, scope: str):
        self.scope = scope  # "daily" | "monthly"
        super().__init__(f"{scope} import limit reached")


def _get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _get_ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.client("dynamodb")
    return _ddb


def _check_and_increment_quota(user_id: str) -> None:
    """Atomically bump the user's daily + monthly counters, but only if BOTH are
    still under their limits. Uses a single transaction with a conditional update
    per window, so nothing is counted when a limit is hit (no drift) and it's
    race-safe across concurrent requests. Raises QuotaExceeded on the breached
    window. Fails OPEN on any unexpected DynamoDB error — a counter-store blip
    should never block all imports (the limits are cost control, not security)."""
    if not QUOTA_TABLE:
        return

    now = datetime.now(timezone.utc)
    day_key = f"{user_id}#DAY#{now:%Y-%m-%d}"
    month_key = f"{user_id}#MONTH#{now:%Y-%m}"
    now_epoch = int(time.time())
    day_ttl = now_epoch + 2 * 86400     # clean up ~2 days after the day
    month_ttl = now_epoch + 40 * 86400  # clean up ~40 days after the month

    def update_item(key: str, limit: int, ttl: int) -> dict:
        return {
            "Update": {
                "TableName": QUOTA_TABLE,
                "Key": {"pk": {"S": key}},
                "UpdateExpression": "ADD #c :one SET #t = :ttl",
                "ConditionExpression": "attribute_not_exists(#c) OR #c < :limit",
                "ExpressionAttributeNames": {"#c": "count", "#t": "ttl"},
                "ExpressionAttributeValues": {
                    ":one": {"N": "1"},
                    ":ttl": {"N": str(ttl)},
                    ":limit": {"N": str(limit)},
                },
            }
        }

    ddb = _get_ddb()
    try:
        ddb.transact_write_items(TransactItems=[
            update_item(day_key, DAILY_LIMIT, day_ttl),      # index 0
            update_item(month_key, MONTHLY_LIMIT, month_ttl),  # index 1
        ])
    except ddb.exceptions.TransactionCanceledException as e:
        reasons = e.response.get("CancellationReasons", [])
        if len(reasons) >= 1 and reasons[0].get("Code") == "ConditionalCheckFailed":
            raise QuotaExceeded("daily")
        if len(reasons) >= 2 and reasons[1].get("Code") == "ConditionalCheckFailed":
            raise QuotaExceeded("monthly")
        # Cancelled for some other reason — treat as daily to be safe.
        raise QuotaExceeded("daily")
    except Exception as e:
        # Fail open on infrastructure errors (throttle/outage) — don't block imports.
        logger.error(f"Quota check failed open for user {user_id}: {e}")


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _authenticated_uid(event) -> str | None:
    """Pull the verified uid the authorizer injected into the request context.
    Returns None when it's missing (which should be impossible once the
    authorizer is attached, but we fail closed rather than trust the body)."""
    ctx = (event or {}).get("requestContext", {}) or {}
    authorizer = ctx.get("authorizer", {}) or {}
    uid = authorizer.get("uid")
    if uid:
        return uid
    # Some REST API configurations nest the context one level deeper.
    claims = authorizer.get("claims", {}) or {}
    return claims.get("uid") or claims.get("sub")


def handler(event, context):
    try:
        if not QUEUE_URL:
            logger.error("RECIPE_QUEUE_URL is not configured")
            return _response(500, {"error": "Server not configured"})

        user_id = _authenticated_uid(event)
        if not user_id:
            # No verified identity — the authorizer should have blocked this.
            return _response(401, {"error": "Unauthorized"})

        raw_body = event.get("body")
        if isinstance(raw_body, str):
            try:
                body = json.loads(raw_body) if raw_body else {}
            except json.JSONDecodeError:
                return _response(400, {"error": "Body must be valid JSON"})
        elif isinstance(raw_body, dict):
            body = raw_body
        else:
            body = {}

        user_input = body.get("input")
        request_id = body.get("requestId")
        source_url = body.get("sourceURL")

        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            return _response(400, {"error": "Missing 'input' in request"})
        if len(user_input) > MAX_INPUT_CHARS:
            return _response(400, {"error": "Input too large"})
        if not request_id or not isinstance(request_id, str):
            return _response(400, {"error": "Missing 'requestId' in request"})
        if source_url is not None and (not isinstance(source_url, str) or not _URL_RE.match(source_url)):
            return _response(400, {"error": "Invalid 'sourceURL'"})

        # SSRF guard: reject URLs pointing at disallowed hosts or private/internal
        # addresses before the worker ever fetches them.
        try:
            assert_safe_import(user_input)
            if source_url:
                assert_safe_import(source_url)
        except UnsafeURLError as e:
            logger.info(f"Blocked unsafe import URL for user {user_id}: {e}")
            return _response(400, {"error": "unsafe_url", "message": "That link isn't allowed."})

        # Enforce per-user import limits before doing any work.
        try:
            _check_and_increment_quota(user_id)
        except QuotaExceeded as q:
            logger.info(f"Quota exceeded ({q.scope}) for user {user_id}")
            message = (
                "You've reached your daily import limit. Try again tomorrow."
                if q.scope == "daily"
                else "You've reached your monthly import limit."
            )
            return _response(429, {
                "error": "quota_exceeded",
                "scope": q.scope,
                "message": message,
            })

        # Hand the job to the worker. userId comes from the verified token, NOT
        # from anything the client sent.
        message = {
            "input": user_input.strip(),
            "userId": user_id,
            "sourceURL": source_url,
            "requestId": request_id,
        }

        _get_sqs().send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message),
            # Dedupe retries of the same client request at the producer: the
            # worker is idempotent too, but this avoids enqueuing twice.
            MessageAttributes={
                "requestId": {"DataType": "String", "StringValue": request_id},
                "userId": {"DataType": "String", "StringValue": user_id},
            },
        )

        logger.info(f"Enqueued recipe import requestId={request_id} user={user_id}")
        # 202: accepted for async processing. The result arrives via the Firestore
        # recipeReady event, matched back by requestId.
        return _response(202, {"status": "accepted", "requestId": request_id})

    except Exception as e:
        logger.error(f"Enqueue handler error: {e}")
        return _response(500, {"error": "Failed to enqueue import"})
