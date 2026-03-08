import re
import json
import logging
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from models import Ingredient, TikTokRecipeProcessorService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class WebRecipeProcessor:

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    # -----------------------------
    # 1️⃣ Get recipe image
    # -----------------------------
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
            except:
                continue

        return None

    # -----------------------------
    # 2️⃣ Extract Schema.org recipe
    # -----------------------------
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

            except:
                continue

        return None

    # -----------------------------
    # 3️⃣ Parse recipe fields
    # -----------------------------
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

        return {
            "title": title,
            "ingredients": ingredients_raw,
            "instructions": instructions
        }

    # -----------------------------
    # 4️⃣ AI fallback
    # -----------------------------
    def ai_fallback(self, text: str) -> dict:
        logger.info("Using AI fallback for recipe extraction")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": """
Extract recipe title, ingredients and instructions from the text.

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
                {"role": "user", "content": text[:15000]}
            ]
        )
        return json.loads(completion.choices[0].message.content)

    # -----------------------------
    # 5️⃣ Parse ingredients into Ingredient objects
    # -----------------------------
    def parse_ingredients(self, ingredients_raw: list) -> list[Ingredient]:
        if not ingredients_raw:
            return []

        if isinstance(ingredients_raw[0], dict):
            return [
                Ingredient(
                    name=i.get("name", ""),
                    quantity=i.get("quantity"),
                    unit=i.get("unit")
                )
                for i in ingredients_raw
            ]

        logger.info("Structuring plain string ingredients via OpenAI")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": """
Split each ingredient string into name, quantity, and unit.

Return JSON exactly like:
{
  "ingredients": [
    { "name": "all-purpose flour", "quantity": "1.5", "unit": "cups" },
    { "name": "egg", "quantity": "1", "unit": null }
  ]
}
"""
                },
                {"role": "user", "content": json.dumps(ingredients_raw)}
            ]
        )
        structured = json.loads(completion.choices[0].message.content).get("ingredients", [])
        return [
            Ingredient(
                name=i.get("name", ""),
                quantity=i.get("quantity"),
                unit=i.get("unit")
            )
            for i in structured
        ]

    # -----------------------------
    # 6️⃣ Strip step prefixes defensively
    # -----------------------------
    def strip_step_prefixes(self, instructions: list[str]) -> list[str]:
        return [re.sub(r"^Step\s*\d+:\s*", "", step) for step in instructions]

    # -----------------------------
    # 7️⃣ Full pipeline
    # -----------------------------
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

        logger.info(f"Successfully processed recipe: {raw.get('title', '')}")

        return TikTokRecipeProcessorService(
            title=raw.get("title", ""),
            ingredients=ingredients,
            instructions=instructions,
            image=image
        )