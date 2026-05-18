import os
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


# -----------------------------
# Detect input type
# -----------------------------
def detect_input_type(user_input: str) -> str:
    user_input = user_input.strip()
    if user_input.startswith("http://") or user_input.startswith("https://"):
        if "tiktok.com" in user_input:
            return "tiktok"
        if "instagram.com" in user_input:
            return "instagram"
        return "url"
    return "text"


# -----------------------------
# Handler
# -----------------------------
def handler(event, context):
    try:
        # Support both direct invocation and Lambda Function URL
        if "body" in event:
            body = json.loads(event["body"]) if isinstance(event["body"], str) else event["body"]
            user_input = body.get("input")
            user_id = body.get("userId")
        else:
            user_input = event.get("input")
            user_id = event.get("userId")

        if not user_input:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'input' in request"})
            }

        if not user_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'userId' in request"})
            }

        input_type = detect_input_type(user_input)
        logger.info(f"Detected input type: {input_type}")

        if input_type == "tiktok":
            processor = TikTokRecipeProcessor(
                api_key=OPENAI_API_KEY,
                media_lambda_name=MEDIA_LAMBDA_NAME
            )
        elif input_type == "instagram":
            processor = InstagramRecipeProcessor(
                api_key=OPENAI_API_KEY,
                media_lambda_name=INSTAGRAM_MEDIA_LAMBDA_NAME
            )
        elif input_type == "url":
            processor = WebRecipeProcessor(api_key=OPENAI_API_KEY)
        else:
            processor = TextRecipeProcessor(api_key=OPENAI_API_KEY)

        recipe = processor.process(user_input)

        recipe_data = {
            "title": recipe.title,
            "image": recipe.image,
            "ingredients": [
                {
                    "name": i.name,
                    "quantity": i.quantity,
                    "unit": i.unit,
                    "emojiIcon": i.emojiIcon
                }
                for i in recipe.ingredients
            ],
            "instructions": recipe.instructions
        }

        # Write recipeReady event to Firestore
        write_recipe_ready_event(user_id=user_id, recipe_data=recipe_data)

        return {
            "statusCode": 200,
            "body": json.dumps(recipe_data, ensure_ascii=False)
        }

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }


# -----------------------------
# Instagram Media Lambda handler (separate Lambda entrypoint)
# -----------------------------
def instagram_media_handler(event, context):
    from InstagramMediaProcessor import InstagramMediaProcessor

    try:
        url = event.get("url")

        if not url:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'url' in request"})
            }

        processor = InstagramMediaProcessor(
            api_key=OPENAI_API_KEY,
            cookies_bucket=os.environ.get("COOKIES_BUCKET"),
            cookies_key=os.environ.get("COOKIES_KEY", "cookies/instagram_cookies.txt")
        )
        media_payload = processor.process(url)

        return {
            "statusCode": 200,
            "body": json.dumps(media_payload)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }