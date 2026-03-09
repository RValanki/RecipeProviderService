import re
import json
import boto3
import logging
from openai import OpenAI
from models import Ingredient, TikTokRecipeProcessorService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TikTokRecipeProcessor:

    def __init__(self, api_key: str, media_lambda_name: str):
        self.client = OpenAI(api_key=api_key)
        self.lambda_client = boto3.client("lambda")
        self.media_lambda_name = media_lambda_name

    # -----------------------------
    # 1️⃣ Invoke TikTokMediaProcessor Lambda
    # -----------------------------
    def invoke_media_processor(self, url: str) -> dict:
        logger.info(f"Invoking media processor Lambda for URL: {url}")

        response = self.lambda_client.invoke(
            FunctionName=self.media_lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"url": url})
        )

        payload = json.loads(response["Payload"].read())

        if response.get("FunctionError"):
            error = payload.get("errorMessage", "Unknown Lambda error")
            logger.error(f"Media processor Lambda failed: {error}")
            raise RuntimeError(f"Media processor Lambda failed: {error}")

        if payload.get("statusCode") != 200:
            error = json.loads(payload.get("body", "{}")).get("error", "Unknown error")
            logger.error(f"Media processor returned error: {error}")
            raise RuntimeError(f"Media processor returned error: {error}")

        return json.loads(payload["body"])

    # -----------------------------
    # 2️⃣ Combine text sources
    # -----------------------------
    def combine_text(self, title: str, description: str, transcript: str) -> str:
        return f"""
TIKTOK TITLE:
{title}

TIKTOK CAPTION / DESCRIPTION:
{description}

VIDEO TRANSCRIPT:
{transcript}

Use all three sources to extract the most accurate recipe possible.
If ingredients or instructions appear in any section, include them.
"""

    # -----------------------------
    # 3️⃣ Normalize recipe title
    # -----------------------------
    def normalize_recipe_title(self, raw_title: str) -> str:
        logger.info("Normalizing recipe title")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
You are given a raw TikTok video title for a cooking video.
Extract and return only the clean, standard recipe name.

Rules:
- Remove hashtags, emojis, filler phrases like "the best", "easy", "you need to try this"
- Remove creator names or personal commentary
- Return a short, standard recipe title like you'd see in a cookbook (e.g. "Butter Chicken", "Classic Tiramisu")
- Return only the recipe name, nothing else
"""
                },
                {"role": "user", "content": raw_title}
            ]
        )
        return completion.choices[0].message.content.strip()

    # -----------------------------
    # 4️⃣ Extract recipe JSON
    # -----------------------------
    def extract_recipe_from_text(self, text: str) -> dict:
        logger.info("Extracting recipe from text")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": """
You are a recipe extraction assistant. Extract a recipe from the provided text which may include a video title, caption, and spoken transcript from a cooking video.

Rules:
- Instructions may be spoken conversationally (e.g. "first you caramelize the onions") — convert these into clean steps
- If the transcript contains any cooking actions (cook, add, mix, heat, stir, etc.), turn them into instructions
- Infer logical steps if the transcript is incomplete but ingredients are mentioned
- Never return an empty instructions array if there is any cooking-related content
- For each ingredient, split it into name, quantity, and unit
- For each ingredient, also provide an emojiIcon that best represents it (e.g. "🧄" for garlic, "🥚" for egg)
- If you cannot find a suitable emoji for an ingredient, default to "🍽️"
- Return instructions as plain sentences, no "Step 1:", "Step 2:" prefixes

Return JSON exactly like:
{
  "ingredients": [
    { "name": "all-purpose flour", "quantity": "1.5", "unit": "cups", "emojiIcon": "🌾" },
    { "name": "egg", "quantity": "1", "unit": null, "emojiIcon": "🥚" }
  ],
  "instructions": ["...", "...", "..."]
}
"""
                },
                {"role": "user", "content": text[:12000]}
            ]
        )
        return json.loads(completion.choices[0].message.content)

    # -----------------------------
    # 5️⃣ Strip step prefixes defensively
    # -----------------------------
    def strip_step_prefixes(self, instructions: list[str]) -> list[str]:
        return [re.sub(r"^Step\s*\d+:\s*", "", step) for step in instructions]

    # -----------------------------
    # Full pipeline
    # -----------------------------
    def process(self, url: str) -> TikTokRecipeProcessorService:
        logger.info(f"Processing TikTok URL: {url}")

        media_payload = self.invoke_media_processor(url)

        title = media_payload.get("title", "")
        description = media_payload.get("description", "")
        transcript = media_payload.get("transcript", "")
        thumbnail_url = media_payload.get("thumbnail_url")

        combined_text = self.combine_text(title, description, transcript)
        raw_recipe = self.extract_recipe_from_text(combined_text)
        normalized_title = self.normalize_recipe_title(title)

        ingredients = [
            Ingredient(
                name=i.get("name", ""),
                quantity=i.get("quantity"),
                unit=i.get("unit"),
                emojiIcon=i.get("emojiIcon")
            )
            for i in raw_recipe.get("ingredients", [])
        ]

        instructions = self.strip_step_prefixes(raw_recipe.get("instructions", []))

        logger.info(f"Successfully processed recipe: {normalized_title}")

        return TikTokRecipeProcessorService(
            title=normalized_title,
            ingredients=ingredients,
            instructions=instructions,
            image=thumbnail_url
        )