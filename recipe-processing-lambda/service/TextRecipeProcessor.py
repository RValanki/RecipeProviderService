import json
import logging
from openai import OpenAI
from models import Ingredient, TikTokRecipeProcessorService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TextRecipeProcessor:

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    # -----------------------------
    # 1️⃣ Extract recipe from text
    # -----------------------------
    def extract_recipe_from_text(self, text_chunk: str) -> dict:
        logger.info("Extracting recipe from text")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": """
You are a helpful assistant that extracts recipes from text.

For each ingredient, split it into name, quantity, and unit.

Return JSON exactly like:
{
  "title": "",
  "ingredients": [
    { "name": "all-purpose flour", "quantity": "1.5", "unit": "cups" },
    { "name": "egg", "quantity": "1", "unit": null }
  ],
  "instructions": ["...", "...", "..."]
}

Rules:
- Return instructions as plain sentences, no "Step 1:", "Step 2:" prefixes
"""
                },
                {"role": "user", "content": text_chunk[:12000]}
            ]
        )
        return json.loads(completion.choices[0].message.content)

    # -----------------------------
    # 2️⃣ Full pipeline
    # -----------------------------
    def process(self, text_chunk: str) -> TikTokRecipeProcessorService:
        logger.info("Processing text recipe")

        raw = self.extract_recipe_from_text(text_chunk)
        title = raw.get("title", "")

        ingredients = [
            Ingredient(
                name=i.get("name", ""),
                quantity=i.get("quantity"),
                unit=i.get("unit")
            )
            for i in raw.get("ingredients", [])
        ]

        logger.info(f"Successfully processed recipe: {title}")

        return TikTokRecipeProcessorService(
            title=title,
            ingredients=ingredients,
            instructions=raw.get("instructions", []),
            image=None
        )