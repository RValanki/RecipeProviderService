import re
import json
import boto3
import logging
from openai import OpenAI
from models import Ingredient, TikTokRecipeProcessorService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


INGREDIENT_PROMPT = """
You are a recipe extraction assistant. Extract a recipe from the provided text which may include a video title, caption, and spoken transcript from a cooking video.

Rules:
- Instructions may be spoken conversationally — convert these into clean steps
- If the transcript contains any cooking actions (cook, add, mix, heat, stir, etc.), turn them into instructions
- Infer logical steps if the transcript is incomplete but ingredients are mentioned
- Never return an empty instructions array if there is any cooking-related content
- Return instructions as plain sentences, no "Step 1:", "Step 2:" prefixes
- For each ingredient provide:
  - name: the ingredient name
  - emoji: a single relevant food emoji for the ingredient — use your best guess (e.g. "🧄" for garlic, "🥚" for egg, "🍗" for chicken). Default to "🍽️" only if no better emoji exists
  - quantity: the amount as a number — if not mentioned, use your culinary knowledge to infer a typical quantity for the recipe context
  - unit: always use the most appropriate culinary unit (tsp, tbsp, cups, ml, g, cloves, pieces etc.) — NEVER use "pcs" for spices, liquids, powders, or anything with a standard culinary unit. Only use "pcs" for whole countable items like eggs or whole chicken thighs
  - totalGram: ALWAYS provide your best gram estimate — never null. Use culinary knowledge to estimate:
      1 tsp ground spice ≈ 3g, 1 tbsp ≈ 9g, 1 cup flour ≈ 120g, 1 cup liquid ≈ 240g,
      1 tbsp oil ≈ 14g, 1 tbsp butter ≈ 14g, 1 clove garlic ≈ 3g, 1 large egg ≈ 50g,
      1 tbsp honey ≈ 21g, 1 tbsp soy sauce ≈ 17g, 1 cup broth ≈ 240g
  - gramPerUnit: ALWAYS provide the gram weight of one single unit — never null. Same inference rules apply.

Return JSON exactly like:
{
  "ingredients": [
    { "name": "smoked paprika", "emoji": "🌶️", "quantity": 1, "unit": "tbsp", "totalGram": 9.0, "gramPerUnit": 9.0 },
    { "name": "garlic", "emoji": "🧄", "quantity": 2, "unit": "cloves", "totalGram": 6.0, "gramPerUnit": 3.0 },
    { "name": "chicken breast", "emoji": "🍗", "quantity": 500, "unit": "g", "totalGram": 500.0, "gramPerUnit": 1.0 },
    { "name": "honey", "emoji": "🍯", "quantity": 0.25, "unit": "cups", "totalGram": 85.0, "gramPerUnit": 340.0 },
    { "name": "egg", "emoji": "🥚", "quantity": 2, "unit": "pcs", "totalGram": 100.0, "gramPerUnit": 50.0 }
  ],
  "instructions": ["...", "...", "..."]
}
"""


def parse_ingredients(raw: list) -> list[Ingredient]:
    return [
        Ingredient(
            name=i.get("name", ""),
            emoji=i.get("emoji", "🍽️"),
            quantity=float(i["quantity"]) if i.get("quantity") is not None else None,
            unit=i.get("unit"),
            totalGram=float(i["totalGram"]) if i.get("totalGram") is not None else None,
            gramPerUnit=float(i["gramPerUnit"]) if i.get("gramPerUnit") is not None else None
        )
        for i in raw
    ]


class TikTokRecipeProcessor:

    def __init__(self, api_key: str, media_lambda_name: str):
        self.client = OpenAI(api_key=api_key)
        self.lambda_client = boto3.client("lambda")
        self.media_lambda_name = media_lambda_name

    def invoke_media_processor(self, url: str) -> dict:
        logger.info(f"Invoking media processor Lambda for URL: {url}")
        response = self.lambda_client.invoke(
            FunctionName=self.media_lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"url": url})
        )
        payload = json.loads(response["Payload"].read())
        if response.get("FunctionError"):
            raise RuntimeError(f"Media processor Lambda failed: {payload.get('errorMessage', 'Unknown error')}")
        if payload.get("statusCode") != 200:
            raise RuntimeError(f"Media processor returned error: {json.loads(payload.get('body', '{}')).get('error', 'Unknown error')}")
        return json.loads(payload["body"])

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
        logger.info(f"Processing TikTok URL: {url}")
        media_payload = self.invoke_media_processor(url)
        title = media_payload.get("title", "")
        description = media_payload.get("description", "")
        transcript = media_payload.get("transcript", "")
        thumbnail_url = media_payload.get("thumbnail_url")

        combined_text = self.combine_text(title, description, transcript)
        raw_recipe = self.extract_recipe_from_text(combined_text)
        normalized_title = self.normalize_recipe_title(title)

        ingredients = parse_ingredients(raw_recipe.get("ingredients", []))
        instructions = self.strip_step_prefixes(raw_recipe.get("instructions", []))
        logger.info(f"Successfully processed recipe: {normalized_title}")

        return TikTokRecipeProcessorService(
            title=normalized_title,
            ingredients=ingredients,
            instructions=instructions,
            image=thumbnail_url
        )