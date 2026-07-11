import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from models import TikTokRecipeProcessorService
from TikTokRecipeProcessor import INGREDIENT_PROMPT, parse_ingredients

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WEB_FALLBACK_PROMPT = INGREDIENT_PROMPT + """
Also extract the recipe title and include it in the response.

Return JSON exactly like:
{
  "title": "",
  "totalTime": 45,
  "ingredients": [...],
  "instructions": [...]
}
"""

WEB_STRUCTURE_PROMPT = """
Split each ingredient string into name, emoji, quantity, unit, totalGram, and gramPerUnit.

Rules:
- name: the ingredient in its most basic, constituent form — no preparation descriptors (e.g. "garlic" not "crushed garlic", "chicken breast" not "diced chicken breast", "onion" not "finely chopped onion"). Strip all adjectives describing cut, texture, or preparation state.
- emoji: a single relevant food emoji for the ingredient — use your best guess (e.g. "🧄" for garlic, "🥚" for egg, "🍗" for chicken). Default to "🍽️" only if no better emoji exists
- quantity must be a number (e.g. 1.5), never a string — if unknown use your culinary knowledge to infer a typical quantity
- unit: always use the most appropriate culinary unit (tsp, tbsp, cups, ml, g, cloves etc.) — NEVER use "pcs" for spices, liquids or powders. Only use "pcs" for whole countable items like eggs or whole chicken thighs
- totalGram: ALWAYS provide your best gram estimate — never null. Use culinary knowledge:
    1 tsp ground spice ≈ 3g, 1 tbsp ≈ 9g, 1 cup flour ≈ 120g, 1 cup liquid ≈ 240g,
    1 tbsp oil ≈ 14g, 1 tbsp butter ≈ 14g, 1 clove garlic ≈ 3g, 1 large egg ≈ 50g,
    1 tbsp honey ≈ 21g, 1 tbsp soy sauce ≈ 17g, 1 cup broth ≈ 240g
- gramPerUnit: ALWAYS provide the gram weight of one single unit — never null

Return JSON exactly like:
{
  "ingredients": [
    { "name": "garlic", "emoji": "🧄", "quantity": 2, "unit": "cloves", "totalGram": 6.0, "gramPerUnit": 3.0 },
    { "name": "salt", "emoji": "🧂", "quantity": 1, "unit": "tsp", "totalGram": 6.0, "gramPerUnit": 6.0 }
  ]
}
"""


def _parse_iso8601_duration(duration: str | None) -> int | None:
    """Parses schema.org durations like 'PT45M', 'PT1H30M' into total minutes."""
    if not duration or not isinstance(duration, str):
        return None
    match = re.match(r'^P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?$', duration)
    if not match:
        return None
    hours, minutes = match.groups()
    if not hours and not minutes:
        return None
    return (int(hours) * 60 if hours else 0) + (int(minutes) if minutes else 0)


class WebRecipeProcessor:

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    def get_recipe_image(self, soup) -> str | None:
        og_image = soup.find("meta", property="og:image")
        if og_image:
            return og_image.get("content")
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "Recipe"), {})
                if data.get("@type") == "Recipe":
                    image = data.get("image")
                    if isinstance(image, list):
                        return image[0]
                    if isinstance(image, dict):
                        return image.get("url")
                    return image
            except Exception as e:
                logger.debug(f"Skipping LD+JSON block: {e}")
                continue
        return None

    def extract_schema_recipe(self, html: str) -> dict | None:
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Recipe":
                            return self.parse_recipe(item)
                if data.get("@type") == "Recipe":
                    return self.parse_recipe(data)
            except Exception as e:
                logger.debug(f"Skipping LD+JSON block: {e}")
                continue
        return None

    def parse_recipe(self, recipe: dict) -> dict:
        title = recipe.get("name", "")
        ingredients_raw = recipe.get("recipeIngredient", [])
        instructions_raw = recipe.get("recipeInstructions", [])
        instructions = []
        for step in instructions_raw:
            if isinstance(step, dict):
                instructions.append(step.get("text"))
            else:
                instructions.append(step)

        total_time = _parse_iso8601_duration(recipe.get("totalTime"))
        if total_time is None:
            prep = _parse_iso8601_duration(recipe.get("prepTime"))
            cook = _parse_iso8601_duration(recipe.get("cookTime"))
            if prep or cook:
                total_time = (prep or 0) + (cook or 0)

        return {
            "title": title,
            "ingredients": ingredients_raw,
            "instructions": instructions,
            "totalTime": total_time
        }

    def ai_fallback(self, text: str) -> dict:
        logger.info("Using AI fallback for recipe extraction")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": WEB_FALLBACK_PROMPT},
                {"role": "user", "content": text[:15000]}
            ]
        )
        return json.loads(completion.choices[0].message.content)

    def estimate_total_time(self, title: str, ingredients_raw: list, instructions: list[str]) -> int:
        logger.info("No totalTime found in schema data — estimating via AI")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": """
You are a culinary assistant. Given a recipe title, ingredients, and instructions,
estimate the total time (prep + cook) to make it, in minutes, as an integer.
Return JSON exactly like: {"totalTime": 45}
"""
                },
                {"role": "user", "content": json.dumps({
                    "title": title,
                    "ingredients": ingredients_raw,
                    "instructions": instructions
                })}
            ]
        )
        result = json.loads(completion.choices[0].message.content)
        return result.get("totalTime", 30)

    def parse_ingredients(self, ingredients_raw: list) -> list:
        if not ingredients_raw:
            return []

        if isinstance(ingredients_raw[0], dict):
            return parse_ingredients(ingredients_raw)

        logger.info("Structuring plain string ingredients via OpenAI")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": WEB_STRUCTURE_PROMPT},
                {"role": "user", "content": json.dumps(ingredients_raw)}
            ]
        )
        structured = json.loads(completion.choices[0].message.content).get("ingredients", [])
        return parse_ingredients(structured)

    def strip_step_prefixes(self, instructions: list[str]) -> list[str]:
        return [re.sub(r"^Step\s*\d+:\s*", "", step) for step in instructions]

    def process(self, url: str) -> TikTokRecipeProcessorService:
        logger.info(f"Processing web URL: {url}")
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            html = response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch URL: {e}")
            raise RuntimeError(f"Failed to fetch URL: {e}")

        soup = BeautifulSoup(html, "html.parser")
        image = self.get_recipe_image(soup)

        schema_recipe = self.extract_schema_recipe(html)
        if schema_recipe:
            logger.info("Schema recipe found")
            raw = schema_recipe
        else:
            logger.info("No schema recipe found, falling back to AI")
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
            raw = self.ai_fallback(text)

        ingredients = self.parse_ingredients(raw.get("ingredients", []))
        instructions = self.strip_step_prefixes(raw.get("instructions", []))

        total_time = raw.get("totalTime")
        if total_time is None:
            total_time = self.estimate_total_time(raw.get("title", ""), raw.get("ingredients", []), instructions)

        logger.info(f"Successfully processed recipe: {raw.get('title', '')}")

        return TikTokRecipeProcessorService(
            title=raw.get("title", ""),
            ingredients=ingredients,
            instructions=instructions,
            image=image,
            totalTime=total_time
        )