import re
import json
import logging
from openai import OpenAI
from models import TikTokRecipeProcessorService
from TikTokRecipeProcessor import INGREDIENT_PROMPT, parse_ingredients

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TEXT_PROMPT = INGREDIENT_PROMPT + """
Also extract the recipe title and include it in the response.

Return JSON exactly like:
{
  "title": "",
  "totalTime": 45,
  "ingredients": [...],
  "instructions": [...]
}
"""


class TextRecipeProcessor:

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    def extract_recipe_from_text(self, text_chunk: str) -> dict:
        logger.info("Extracting recipe from text")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": TEXT_PROMPT},
                {"role": "user", "content": text_chunk[:12000]}
            ]
        )
        return json.loads(completion.choices[0].message.content)

    def strip_step_prefixes(self, instructions: list[str]) -> list[str]:
        return [re.sub(r"^Step\s*\d+:\s*", "", step) for step in instructions]

    def process(self, text_chunk: str) -> TikTokRecipeProcessorService:
        logger.info("Processing text recipe")
        raw = self.extract_recipe_from_text(text_chunk)
        title = raw.get("title", "")
        ingredients = parse_ingredients(raw.get("ingredients", []))
        instructions = self.strip_step_prefixes(raw.get("instructions", []))
        logger.info(f"Successfully processed recipe: {title}")

        return TikTokRecipeProcessorService(
            title=title,
            ingredients=ingredients,
            instructions=instructions,
            image=None,
            totalTime=raw.get("totalTime")
        )