import os
import re
import json
import logging
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
        "foodQuantity": food_quantity
    }


def handler(event, context):
    try:
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
            user_input = body.get("input")
            user_id = body.get("userId")
        else:
            user_input = event.get("input")
            user_id = event.get("userId")

        if not user_input:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'input' in request"})}
        if not user_id:
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'userId' in request"})}

        input_type, user_input = detect_input_type(user_input)
        logger.info(f"Detected input type: {input_type}, resolved input: {user_input}")

        if input_type == "tiktok":
            processor = TikTokRecipeProcessor(api_key=OPENAI_API_KEY, media_lambda_name=MEDIA_LAMBDA_NAME)
        elif input_type == "instagram":
            processor = InstagramRecipeProcessor(api_key=OPENAI_API_KEY, media_lambda_name=INSTAGRAM_MEDIA_LAMBDA_NAME)
        elif input_type == "url":
            processor = WebRecipeProcessor(api_key=OPENAI_API_KEY)
        else:
            processor = TextRecipeProcessor(api_key=OPENAI_API_KEY)

        recipe = processor.process(user_input)

        recipe_data = {
            "title": recipe.title,
            "image": recipe.image,
            "ingredients": [_serialize_ingredient(i) for i in recipe.ingredients],
            "instructions": recipe.instructions
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