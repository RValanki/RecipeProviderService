import os
import re
import json
import logging
from openai import OpenAI
from TikTokRecipeProcessor import TikTokRecipeProcessor
from InstagramRecipeProcessor import InstagramRecipeProcessor
from WebRecipeProcessor import WebRecipeProcessor
from TextRecipeProcessor import TextRecipeProcessor
from FirestoreService import write_recipe_ready_event

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MEDIA_LAMBDA_NAME = os.environ.get("MEDIA_LAMBDA_NAME")
INSTAGRAM_MEDIA_LAMBDA_NAME = os.environ.get("INSTAGRAM_MEDIA_LAMBDA_NAME")


def extract_url(text: str) -> str | None:
    match = re.search(r'https?://\S+', text)
    return match.group(0) if match else None


def detect_input_type(user_input: str) -> tuple[str, str]:
    user_input = user_input.strip()
    url = extract_url(user_input)
    if url:
        if "tiktok.com" in url:
            return "tiktok", url
        if "instagram.com" in url:
            return "instagram", url
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


def handler(event, context):
    try:
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
            user_input = body.get("input")
            user_id = body.get("userId")
            source_url = body.get("sourceURL")
            request_id = body.get("requestId")
        else:
            user_input = event.get("input")
            user_id = event.get("userId")
            source_url = event.get("sourceURL")
            request_id = event.get("requestId")

        if not user_input:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'input' in request"})}
        if not user_id:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'userId' in request"})}

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

        # Safety net: if the source didn't yield a usable title, have the AI name
        # the dish from its ingredients + steps rather than shipping "no title".
        if _is_blank_title(recipe.title):
            logger.info("No usable title from processor — inferring from recipe content")
            recipe.title = _infer_title(recipe.ingredients, recipe.instructions)

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

        return {
            "statusCode": 200,
            "body": json.dumps(recipe_data, ensure_ascii=False)
        }

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


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