import os
import json
import logging
from dataclasses import asdict
from TikTokRecipeProcessor import TikTokRecipeProcessor
from WebRecipeProcessor import WebRecipeProcessor
from TextRecipeProcessor import TextRecipeProcessor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MEDIA_LAMBDA_NAME = os.environ.get("MEDIA_LAMBDA_NAME")


# -----------------------------
# Detect input type
# -----------------------------
def detect_input_type(user_input: str) -> str:
    user_input = user_input.strip()
    if user_input.startswith("http://") or user_input.startswith("https://"):
        if "tiktok.com" in user_input:
            return "tiktok"
        return "url"
    return "text"


# -----------------------------
# Handler
# -----------------------------
def handler(event, context):
    try:
        user_input = event.get("input")

        if not user_input:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'input' in request"})
            }

        input_type = detect_input_type(user_input)
        logger.info(f"Detected input type: {input_type}")

        if input_type == "tiktok":
            processor = TikTokRecipeProcessor(
                api_key=OPENAI_API_KEY,
                media_lambda_name=MEDIA_LAMBDA_NAME
            )
        elif input_type == "url":
            processor = WebRecipeProcessor(api_key=OPENAI_API_KEY)
        else:
            processor = TextRecipeProcessor(api_key=OPENAI_API_KEY)

        recipe = processor.process(user_input)

        return {
            "statusCode": 200,
            "body": json.dumps({
                "title": recipe.title,
                "image": recipe.image,
                "ingredients": [
                    {"name": i.name, "quantity": i.quantity, "unit": i.unit}
                    for i in recipe.ingredients
                ],
                "instructions": recipe.instructions
            }, ensure_ascii=False)
        }

    except Exception as e:
        logger.error(f"Handler error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }