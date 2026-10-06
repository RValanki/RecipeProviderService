import os
import re
import json
import logging
from openai import OpenAI
from TikTokRecipeProcessor import TikTokRecipeProcessor, combine_ingredients
from InstagramRecipeProcessor import InstagramRecipeProcessor
from WebRecipeProcessor import WebRecipeProcessor
from TextRecipeProcessor import TextRecipeProcessor
from FirestoreService import write_recipe_ready_event, write_recipe_failed_event
from ssrf import assert_safe_import, platform_for_url, UnsafeURLError
from ImageRehost import rehost_image

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MEDIA_LAMBDA_NAME = os.environ.get("MEDIA_LAMBDA_NAME")
INSTAGRAM_MEDIA_LAMBDA_NAME = os.environ.get("INSTAGRAM_MEDIA_LAMBDA_NAME")


class NotARecipeError(Exception):
    """The input didn't contain a genuine recipe (off-topic text, a prompt-
    injection attempt, a non-cooking link, etc.). We reject it rather than save
    garbage, and tell the user it wasn't a recipe."""
    pass


class ImageUnavailableError(Exception):
    """A media import (TikTok/Instagram), or a web import that DID provide a photo,
    ended up with no permanently-hostable image. Per product rule we never ship a
    recipe whose image would fail to load, so we fail the import instead. (Text and
    photo-less web imports have no image by design and never raise this.)"""
    pass


def extract_url(text: str) -> str | None:
    match = re.search(r'https?://\S+', text)
    return match.group(0) if match else None


def detect_input_type(user_input: str) -> tuple[str, str]:
    user_input = user_input.strip()
    url = extract_url(user_input)
    if url:
        # Classify by HOST (the same rule the SSRF guard uses), never by
        # substring. A substring check routed e.g. https://evil.com/?x=tiktok.com
        # to the media path and ran yt-dlp against the attacker host, bypassing
        # the platform allow-list the producer thought it had enforced.
        platform = platform_for_url(url)
        if platform:
            return platform, url
        return "url", url
    return "text", user_input


def _serialize_ingredient(i) -> dict:
    food_quantity: dict = {
        "value": float(i.quantity) if i.quantity is not None else 1.0,
        "unit": i.unit or "pcs"
    }
    if i.totalGram is not None:
        food_quantity["totalGram"] = i.totalGram
    if i.gramPerUnit is not None:
        food_quantity["gramPerUnit"] = i.gramPerUnit

    return {
        "name": i.name,
        "emoji": i.emoji,
        "foodQuantity": food_quantity,
        "matchID": i.name
    }


def _is_blank_title(title) -> bool:
    """True when the processor couldn't resolve a real title."""
    if not title:
        return True
    t = title.strip().lower()
    return t == "" or "no title" in t or t in {"untitled", "recipe", "n/a", "none", "unknown"}


def _infer_title(ingredients, instructions) -> str:
    """Best-guess dish name from the recipe's ingredients + steps, used when the
    source provided no usable title."""
    ingredient_names = ", ".join(i.name for i in ingredients[:20] if i.name)
    steps = " ".join(s.text for s in instructions[:8] if s.text)
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a culinary assistant. Given a recipe's ingredients and steps, "
                        "reply with ONLY a short, standard dish name as it would appear in a cookbook "
                        "(e.g. 'Butter Chicken', 'Lemon Garlic Pasta'). No quotes, no extra words."
                    )
                },
                {"role": "user", "content": f"Ingredients: {ingredient_names}\n\nSteps: {steps}"}
            ]
        )
        title = completion.choices[0].message.content.strip().strip('"').strip()
        return title or "Untitled Recipe"
    except Exception as e:
        logger.error(f"Title inference failed: {e}")
        return "Untitled Recipe"


def _serialize_nutrition(n) -> dict | None:
    """Recipe nutrition (whole-recipe + per-serving + serving metadata), or None
    when estimation was skipped/failed."""
    if n is None:
        return None

    def values(v) -> dict:
        return {"calories": v.calories, "protein": v.protein, "fat": v.fat, "carbs": v.carbs}

    return {
        "servings": n.servings,
        "servingSize": n.servingSize,
        "servingSizeGram": n.servingSizeGram,
        "total": values(n.total),
        "perServing": values(n.perServing),
    }


def _serialize_instruction(step) -> dict:
    """One step: text plus optional per-step ingredients (same shape as the
    top-level list) and an optional timer in minutes. Both null when absent."""
    return {
        "text": step.text,
        "timer": step.timer,
        "ingredients": (
            [_serialize_ingredient(i) for i in step.ingredients]
            if step.ingredients else None
        )
    }


def _process_job(user_input, user_id, source_url, request_id) -> dict:
    """Run one recipe import end to end and write the recipeReady event.

    `user_id` is the verified Firebase uid the producer took from the token and
    put on the SQS message — never a client-supplied value. Raises on failure so
    the SQS trigger can retry and, after maxReceiveCount, route the message to the
    DLQ."""
    if not user_input:
        raise ValueError("Missing 'input' in job")
    if not user_id:
        raise ValueError("Missing 'userId' in job")

    # Defense in depth: the producer already ran the SSRF guard before enqueuing,
    # but re-validate at the point of use so a message that reached the queue by
    # any other path can't drive a fetch/yt-dlp at an unsafe host.
    try:
        assert_safe_import(user_input)
        if source_url:
            assert_safe_import(source_url)
    except UnsafeURLError as e:
        raise RuntimeError(f"Unsafe URL: {e}")

    input_type, user_input = detect_input_type(user_input)
    logger.info(f"Detected input type: {input_type}, resolved input: {user_input}")

    # Fall back to the detected URL if the client didn't send one explicitly
    # (older app versions). Stays None for plain-text imports.
    if not source_url and input_type != "text":
        source_url = user_input

    if input_type == "tiktok":
        processor = TikTokRecipeProcessor(api_key=OPENAI_API_KEY, media_lambda_name=MEDIA_LAMBDA_NAME)
    elif input_type == "instagram":
        processor = InstagramRecipeProcessor(api_key=OPENAI_API_KEY, media_lambda_name=INSTAGRAM_MEDIA_LAMBDA_NAME)
    elif input_type == "url":
        processor = WebRecipeProcessor(api_key=OPENAI_API_KEY)
    else:
        processor = TextRecipeProcessor(api_key=OPENAI_API_KEY)

    recipe = processor.process(user_input)

    # Reject anything that isn't a genuine recipe — off-topic text, prompt-injection
    # attempts, non-cooking links. The hardened extraction prompt returns no
    # ingredients/instructions ({"isRecipe": false}) in that case, so there's
    # nothing to build. Bail before inferring a title / calling more models.
    if not recipe.ingredients and not recipe.instructions:
        raise NotARecipeError()

    # Safety net: if the source didn't yield a usable title, have the AI name
    # the dish from its ingredients + steps rather than shipping "no title".
    if _is_blank_title(recipe.title):
        logger.info("No usable title from processor — inferring from recipe content")
        recipe.title = _infer_title(recipe.ingredients, recipe.instructions)

    # Hard requirement: the top-level ingredient list must never repeat an
    # ingredient. Enforce it in code too (the prompt also instructs it), while
    # leaving per-step ingredients free to repeat.
    recipe.ingredients = combine_ingredients(recipe.ingredients)

    # Emojis come straight from the extraction call (INGREDIENT_PROMPT specifies a
    # best-fit emoji per ingredient). We no longer run a second stronger-model pass
    # to re-pick them — that round-trip dominated latency for little gain.

    # Re-host the thumbnail to permanent storage while its CDN URL is still fresh,
    # so the client never depends on a short-lived link. For media imports (and web
    # pages that DID provide a photo) the image is required: if re-hosting fails we
    # fail the whole import rather than ship a recipe whose image would break. Text
    # and photo-less web imports have no image by design and are exempt.
    image_required = input_type in ("tiktok", "instagram") or (input_type == "url" and bool(recipe.image))
    recipe.image = rehost_image(recipe.image, request_id) if recipe.image else None
    if image_required and not recipe.image:
        raise ImageUnavailableError()

    recipe_data = {
        "title": recipe.title,
        "image": recipe.image,
        "totalTime": recipe.totalTime,
        "ingredients": [_serialize_ingredient(i) for i in recipe.ingredients],
        "instructions": [_serialize_instruction(s) for s in recipe.instructions],
        "nutrition": _serialize_nutrition(recipe.nutrition),
        "sourceURL": source_url,   # optional — null for plain-text imports
        "requestId": request_id    # echo back so the client can match the result
    }

    write_recipe_ready_event(user_id=user_id, recipe_data=recipe_data)
    return recipe_data


def handler(event, context):
    """SQS-triggered worker. Each record is one import job enqueued by the
    producer Lambda. Processes with batch size 1; a raised exception fails the
    message so SQS retries it and eventually sends it to the DLQ.

    Falls back to the legacy direct-invoke shape (no `Records`) so the function
    can still be tested with a plain `{input, userId, ...}` payload."""
    records = event.get("Records") if isinstance(event, dict) else None

    if records:
        for record in records:
            try:
                msg = json.loads(record["body"])
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                # Un-parseable message: drop it rather than poison the queue
                # (retrying can never fix malformed JSON).
                logger.error(f"Skipping malformed SQS record: {e}")
                continue

            user_id = msg.get("userId")
            request_id = msg.get("requestId")
            try:
                _process_job(
                    user_input=msg.get("input"),
                    user_id=user_id,
                    source_url=msg.get("sourceURL"),
                    request_id=request_id,
                )
            except NotARecipeError:
                # Off-topic / non-recipe input — tell the user plainly rather than
                # a generic failure, and don't retry (retrying can't make it a recipe).
                logger.info(f"Rejected non-recipe input requestId={request_id}")
                if user_id:
                    try:
                        write_recipe_failed_event(
                            user_id=user_id,
                            request_id=request_id,
                            reason="not_a_recipe",
                            message="That doesn't look like a recipe. Paste a recipe link or the full recipe text.",
                        )
                    except Exception as fe:
                        logger.error(f"Failed to write recipeFailed event: {fe}")
            except ImageUnavailableError:
                # The recipe extracted fine but its image couldn't be fetched/re-hosted.
                # We never ship a media recipe with a broken image, so fail the import.
                logger.info(f"Rejected import — image unavailable requestId={request_id}")
                if user_id:
                    try:
                        write_recipe_failed_event(
                            user_id=user_id,
                            request_id=request_id,
                            reason="image_unavailable",
                            message="We couldn't load this recipe's image. Please try again.",
                        )
                    except Exception as fe:
                        logger.error(f"Failed to write recipeFailed event: {fe}")
            except Exception as e:
                # Caught failure (bad URL, OpenAI error, parse failure, ...). Tell
                # the client immediately with a recipeFailed event and return
                # cleanly so the message is deleted — no SQS retry, no DLQ
                # duplicate. Only an uncaught worker *timeout/crash* falls through
                # to the DLQ consumer (recipe_failure_handler) as a fallback.
                logger.error(f"Recipe import failed requestId={request_id}: {e}")
                if user_id:
                    try:
                        write_recipe_failed_event(user_id=user_id, request_id=request_id, reason=str(e))
                    except Exception as fe:
                        logger.error(f"Failed to write recipeFailed event: {fe}")
        return {"statusCode": 200}

    # Legacy / direct-invoke path (tests, manual invocation).
    try:
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
        else:
            body = event
        recipe_data = _process_job(
            user_input=body.get("input"),
            user_id=body.get("userId"),
            source_url=body.get("sourceURL"),
            request_id=body.get("requestId"),
        )
        return {"statusCode": 200, "body": json.dumps(recipe_data, ensure_ascii=False)}
    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def recipe_failure_handler(event, context):
    """DLQ consumer — fallback failure signal.

    Catches imports whose worker was *killed* (hard timeout / OOM / crash) before
    it could write its own recipeFailed event. Those messages exhaust the main
    queue and land in the DLQ; each one is an import job that never notified the
    client. Writes recipeFailed so the client's pending import resolves instead of
    hanging. (Caught, in-process failures are handled inline by the worker and
    never reach here.)"""
    for record in event.get("Records", []):
        try:
            msg = json.loads(record["body"])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"DLQ: skipping malformed record: {e}")
            continue
        user_id = msg.get("userId")
        request_id = msg.get("requestId")
        if not user_id:
            logger.error("DLQ: message missing userId — cannot notify client")
            continue
        try:
            write_recipe_failed_event(
                user_id=user_id,
                request_id=request_id,
                reason="Import timed out",
            )
        except Exception as e:
            logger.error(f"DLQ: failed to write recipeFailed event: {e}")
    return {"statusCode": 200}


def instagram_media_handler(event, context):
    from InstagramMediaProcessor import InstagramMediaProcessor
    try:
        url = event.get("url")
        if not url:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'url' in request"})}
        processor = InstagramMediaProcessor(
            api_key=OPENAI_API_KEY,
            cookies_bucket=os.environ.get("COOKIES_BUCKET"),
            cookies_key=os.environ.get("COOKIES_KEY", "cookies/instagram_cookies.txt")
        )
        media_payload = processor.process(url)
        return {"statusCode": 200, "body": json.dumps(media_payload)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}