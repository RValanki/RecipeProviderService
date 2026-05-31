import re
import json
import boto3
import logging
from openai import OpenAI
from models import TikTokRecipeProcessorService
from TikTokRecipeProcessor import INGREDIENT_PROMPT, parse_ingredients

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class InstagramRecipeProcessor:

    def __init__(self, api_key: str, media_lambda_name: str):
        self.client = OpenAI(api_key=api_key)
        self.lambda_client = boto3.client("lambda")
        self.media_lambda_name = media_lambda_name

    def invoke_media_processor(self, url: str) -> dict:
        logger.info(f"Invoking Instagram media processor Lambda for URL: {url}")
        response = self.lambda_client.invoke(
            FunctionName=self.media_lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"url": url})
        )
        payload = json.loads(response["Payload"].read())
        if response.get("FunctionError"):
            raise RuntimeError(f"Instagram media processor Lambda failed: {payload.get('errorMessage', 'Unknown error')}")
        if payload.get("statusCode") != 200:
            raise RuntimeError(f"Instagram media processor returned error: {json.loads(payload.get('body', '{}')).get('error', 'Unknown error')}")
        return json.loads(payload["body"])

    def combine_text(self, title: str, description: str, transcript: str) -> str:
        return f"""
INSTAGRAM REEL TITLE:
{title}

INSTAGRAM CAPTION / DESCRIPTION:
{description}

VIDEO TRANSCRIPT:
{transcript}

Use all three sources to extract the most accurate recipe possible.
"""

    def normalize_recipe_title(self, title: str, description: str, transcript: str) -> str:
        logger.info("Normalizing recipe title")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
You are given the title, caption, and transcript of an Instagram cooking video.
Extract and return only the clean, standard recipe name.
Rules:
- Use all three sources to determine what dish is actually being made
- Remove hashtags, emojis, filler phrases like "the best", "easy", "you need to try this"
- Remove creator names or personal commentary
- Return a short, standard recipe title like you'd see in a cookbook (e.g. "Butter Chicken", "Classic Tiramisu")
- Return only the recipe name, nothing else
"""
                },
                {"role": "user", "content": f"TITLE: {title}\n\nCAPTION: {description}\n\nTRANSCRIPT: {transcript[:3000]}"}
            ]
        )
        return completion.choices[0].message.content.strip()

    def extract_recipe_from_text(self, text: str) -> dict:
        logger.info("Extracting recipe from text")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": INGREDIENT_PROMPT},
                {"role": "user", "content": text[:12000]}
            ]
        )
        return json.loads(completion.choices[0].message.content)

    def strip_step_prefixes(self, instructions: list[str]) -> list[str]:
        return [re.sub(r"^Step\s*\d+:\s*", "", step) for step in instructions]

    def process(self, url: str) -> TikTokRecipeProcessorService:
        logger.info(f"Processing Instagram Reel URL: {url}")
        media_payload = self.invoke_media_processor(url)
        title = media_payload.get("title", "")
        description = media_payload.get("description", "")
        transcript = media_payload.get("transcript", "")
        thumbnail_url = media_payload.get("thumbnail_url")

        combined_text = self.combine_text(title, description, transcript)
        raw_recipe = self.extract_recipe_from_text(combined_text)
        normalized_title = self.normalize_recipe_title(title, description, transcript)

        ingredients = parse_ingredients(raw_recipe.get("ingredients", []))
        instructions = self.strip_step_prefixes(raw_recipe.get("instructions", []))
        logger.info(f"Successfully processed Instagram recipe: {normalized_title}")

        return TikTokRecipeProcessorService(
            title=normalized_title,
            ingredients=ingredients,
            instructions=instructions,
            image=thumbnail_url
        )