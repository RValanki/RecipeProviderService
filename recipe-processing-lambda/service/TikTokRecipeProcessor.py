import re
import json
import boto3
import logging
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from models import Ingredient, Instruction, Nutrition, NutritionValues, TikTokRecipeProcessorService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


INGREDIENT_PROMPT = """
You are a recipe extraction assistant. Extract a recipe from the provided text which may include a video title, caption, and spoken transcript from a cooking video.

Rules:
- Instructions may be spoken conversationally — convert these into clean steps
- If the transcript contains any cooking actions (cook, add, mix, heat, stir, etc.), turn them into instructions
- Infer logical steps if the transcript is incomplete but ingredients are mentioned
- Never return an empty instructions array if there is any cooking-related content
- Return "instructions" as an array of STEP OBJECTS, each with exactly these keys:
  - text: the step written as a clean plain sentence, with NO "Step 1:", "Step 2:" prefixes
  - ingredients: the subset of the recipe's ingredients that are actually used or added in THIS step, each as a FULL ingredient object with the same fields and rules as the top-level ingredients (name, emoji, quantity, unit, totalGram, gramPerUnit). The quantity should reflect how much is used in this step. Use null (not an empty array) when the step adds no ingredients — e.g. "preheat the oven", "let it rest", "stir occasionally", "plate and serve".
  - timer: if the step states or implies a specific cooking/waiting duration, the UPPER BOUND of that duration in whole SECONDS as an integer (e.g. "simmer 10-15 minutes" → 900, "bake for 1 hour" → 3600, "rest for 45 seconds" → 45, "sear 30 seconds each side" → 30). Use null when the step mentions no time.
- totalTime: the total time (prep + cook) to make this recipe, in minutes, as an integer.
  If the video/text does not explicitly state a time, use your culinary knowledge to estimate
  a realistic total time based on the ingredients and steps involved. Never return null.
- For each ingredient provide:
  - name: the ingredient in its most basic, constituent form — no preparation descriptors (e.g. "garlic" not "crushed garlic", "chicken breast" not "diced chicken breast", "onion" not "finely chopped onion"). Strip all adjectives describing cut, texture, or preparation state.
  - emoji: a single relevant food emoji for the ingredient — use your best guess (e.g. "🧄" for garlic, "🥚" for egg, "🍗" for chicken). Default to "🍽️" only if no better emoji exists
  - quantity: the amount as a number — if not mentioned, use your culinary knowledge to infer a typical quantity for the recipe context
  - unit: always use the most appropriate culinary unit (tsp, tbsp, cups, ml, g, cloves, pieces etc.) — NEVER use "pcs" for spices, liquids, powders, or anything with a standard culinary unit. Only use "pcs" for whole countable items like eggs or whole chicken thighs
  - totalGram: ALWAYS provide your best gram estimate — never null. Use culinary knowledge to estimate:
      1 tsp ground spice ≈ 3g, 1 tbsp ≈ 9g, 1 cup flour ≈ 120g, 1 cup liquid ≈ 240g,
      1 tbsp oil ≈ 14g, 1 tbsp butter ≈ 14g, 1 clove garlic ≈ 3g, 1 large egg ≈ 50g,
      1 tbsp honey ≈ 21g, 1 tbsp soy sauce ≈ 17g, 1 cup broth ≈ 240g
  - gramPerUnit: ALWAYS provide the gram weight of one single unit — never null. Same inference rules apply.
- nutrition: after finalising the ingredient list above, provide a nutrition object with these fields:
  - total: the TOTAL nutrition for the ENTIRE recipe — the sum across every ingredient as listed, NOT a per-serving value. Base each ingredient's contribution on its totalGram and standard food composition knowledge. Make sure it is realistic for the full amount of food: a substantial main recipe with meat, oil and sauces is typically well over 1000 kcal in total. An object with calories (total kcal), protein, fat and carbs (grams), all numbers, never null.
  - servings: how many servings this recipe realistically yields, as an integer. Choose it so that ONE serving is a NORMAL human portion, not a tiny one. As a guide, one serving of a main dish is roughly 500–800 kcal, and a lighter dish, side or snack is roughly 150–400 kcal. Derive servings from the total: e.g. a recipe totalling ~2000 kcal is about 3–4 servings, ~1200 kcal about 2, ~600 kcal about 1. Do NOT inflate the count — never pick a servings number that makes one serving an unrealistically small portion (e.g. a full meal under ~300 kcal). Never less than 1.
  - servingSize: a short logical-count description of ONE serving, e.g. "2 tacos", "1 bowl", "1 cup", "2 pancakes". Keep it to a countable portion, not a weight.
  Before finalising, sanity-check that total ÷ servings is a believable per-serving calorie amount for this kind of dish; if it is too low, reduce servings and/or raise the total. Do NOT compute per-serving values yourself — only provide total, servings and servingSize.

Return JSON exactly like:
{
  "totalTime": 45,
  "ingredients": [
    { "name": "smoked paprika", "emoji": "🌶️", "quantity": 1, "unit": "tbsp", "totalGram": 9.0, "gramPerUnit": 9.0 },
    { "name": "garlic", "emoji": "🧄", "quantity": 2, "unit": "cloves", "totalGram": 6.0, "gramPerUnit": 3.0 },
    { "name": "chicken breast", "emoji": "🍗", "quantity": 500, "unit": "g", "totalGram": 500.0, "gramPerUnit": 1.0 },
    { "name": "honey", "emoji": "🍯", "quantity": 0.25, "unit": "cups", "totalGram": 85.0, "gramPerUnit": 340.0 },
    { "name": "egg", "emoji": "🥚", "quantity": 2, "unit": "pcs", "totalGram": 100.0, "gramPerUnit": 50.0 }
  ],
  "instructions": [
    { "text": "Season the chicken breast with smoked paprika and salt.", "ingredients": [ { "name": "chicken breast", "emoji": "🍗", "quantity": 500, "unit": "g", "totalGram": 500.0, "gramPerUnit": 1.0 }, { "name": "smoked paprika", "emoji": "🌶️", "quantity": 1, "unit": "tbsp", "totalGram": 9.0, "gramPerUnit": 9.0 } ], "timer": null },
    { "text": "Sauté the garlic in oil until fragrant.", "ingredients": [ { "name": "garlic", "emoji": "🧄", "quantity": 2, "unit": "cloves", "totalGram": 6.0, "gramPerUnit": 3.0 } ], "timer": 120 },
    { "text": "Stir in the honey and simmer until the sauce thickens.", "ingredients": [ { "name": "honey", "emoji": "🍯", "quantity": 0.25, "unit": "cups", "totalGram": 85.0, "gramPerUnit": 340.0 } ], "timer": 900 },
    { "text": "Let the chicken rest, then slice and serve.", "ingredients": null, "timer": 300 }
  ],
  "nutrition": { "servings": 4, "servingSize": "1 bowl", "total": { "calories": 1240.0, "protein": 82.5, "fat": 63.0, "carbs": 74.0 } }
}
"""


CAPTION_EXTRACT_PROMPT = INGREDIENT_PROMPT + """

IMPORTANT — the text you are given is ONLY the CAPTION of a cooking video. It may or may not contain the actual recipe.

First decide whether the caption contains a REAL, usable recipe — it must list ingredients AND at least one preparation/cooking step (or instructions clear enough to actually cook the dish).

- If it does NOT contain a usable recipe (e.g. it is just a hook, hashtags, "full recipe below", a vibe caption, or only names the dish), return EXACTLY:
  {"recipe": null}

- If it DOES contain a usable recipe, also extract the recipe title, and wrap it like:
  {"recipe": {"title": "", "totalTime": 45, "ingredients": [...], "instructions": [...], "nutrition": {"servings": 4, "servingSize": "1 bowl", "total": {"calories": 1240.0, "protein": 82.5, "fat": 63.0, "carbs": 74.0}}}}

Return only JSON, nothing else.
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


def _num(v) -> float:
    """Coerce a JSON value to float, defaulting to 0.0 for null/garbage."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_nutrition(raw: dict, ingredients: list[Ingredient]) -> Nutrition | None:
    """Build a Nutrition from the model's `nutrition` object plus the finalised
    ingredient list. The model supplies `servings`, `servingSize` and the
    whole-recipe `total`; per-serving macros and per-serving grams are computed
    here (total ÷ servings) so they always reconcile with the total. Returns None
    when nutrition is absent/malformed so recipe import still succeeds."""
    data = (raw or {}).get("nutrition")
    if not isinstance(data, dict):
        return None

    total_raw = data.get("total")
    if not isinstance(total_raw, dict):
        return None

    try:
        servings = max(1, int(data.get("servings") or 1))
    except (TypeError, ValueError):
        servings = 1

    total = NutritionValues(
        calories=_num(total_raw.get("calories")),
        protein=_num(total_raw.get("protein")),
        fat=_num(total_raw.get("fat")),
        carbs=_num(total_raw.get("carbs")),
    )

    per_serving = NutritionValues(
        calories=round(total.calories / servings, 1),
        protein=round(total.protein / servings, 1),
        fat=round(total.fat / servings, 1),
        carbs=round(total.carbs / servings, 1),
    )

    # Per-serving weight is derived from the actual ingredient grams, not the model.
    total_gram = sum(i.totalGram for i in ingredients if i.totalGram is not None)
    serving_size_gram = round(total_gram / servings, 1) if total_gram else 0.0

    serving_size = data.get("servingSize")
    serving_size = serving_size.strip() if isinstance(serving_size, str) else ""

    return Nutrition(
        servings=servings,
        servingSize=serving_size,
        servingSizeGram=serving_size_gram,
        total=total,
        perServing=per_serving,
    )


_STEP_PREFIX_RE = re.compile(r"^Step\s*\d+:\s*", re.IGNORECASE)


def _coerce_timer(value) -> int | None:
    """Upper-bound seconds as a positive int, or None."""
    if value is None:
        return None
    try:
        minutes = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return minutes if minutes > 0 else None


def parse_instructions(raw: list) -> list[Instruction]:
    """Parse the model's `instructions` into Instruction objects. Accepts the new
    step-object form ({text, ingredients?, timer?}) and, for resilience, a legacy
    plain-string array."""
    result: list[Instruction] = []
    for item in raw or []:
        if isinstance(item, str):
            text = _STEP_PREFIX_RE.sub("", item).strip()
            if text:
                result.append(Instruction(text=text, ingredients=None, timer=None))
            continue
        if isinstance(item, dict):
            text = _STEP_PREFIX_RE.sub("", (item.get("text") or "")).strip()
            if not text:
                continue
            raw_ings = item.get("ingredients")
            ingredients = parse_ingredients(raw_ings) if raw_ings else None
            result.append(Instruction(
                text=text,
                ingredients=ingredients,
                timer=_coerce_timer(item.get("timer"))
            ))
    return result


class TikTokRecipeProcessor:

    def __init__(self, api_key: str, media_lambda_name: str):
        self.client = OpenAI(api_key=api_key)
        self.lambda_client = boto3.client("lambda")
        self.media_lambda_name = media_lambda_name

    def invoke_media_processor(self, url: str, mode: str = "full") -> dict:
        logger.info(f"Invoking media processor Lambda (mode={mode}) for URL: {url}")
        response = self.lambda_client.invoke(
            FunctionName=self.media_lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"url": url, "mode": mode})
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

    def extract_recipe_from_caption(self, caption: str) -> dict | None:
        """Extract a recipe from the caption alone. Returns None if the caption
        does not contain a usable recipe (model returns {"recipe": null})."""
        logger.info("Attempting recipe extraction from caption")
        completion = self.client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": CAPTION_EXTRACT_PROMPT},
                {"role": "user", "content": caption[:12000]}
            ]
        )
        return json.loads(completion.choices[0].message.content).get("recipe")

    def strip_step_prefixes(self, instructions: list[str]) -> list[str]:
        return [re.sub(r"^Step\s*\d+:\s*", "", step) for step in instructions]

    def process(self, url: str) -> TikTokRecipeProcessorService:
        logger.info(f"Processing TikTok URL: {url}")

        # Start transcription (download + audio + Whisper) in parallel so it's
        # ready if the caption turns out not to hold the recipe. Fetch the
        # lightweight metadata (incl. caption) on this thread meanwhile.
        executor = ThreadPoolExecutor(max_workers=1)
        transcript_future = executor.submit(self.invoke_media_processor, url, "transcribe")
        try:
            meta = self.invoke_media_processor(url, "metadata")
            title = meta.get("title", "")
            description = meta.get("description", "")
            thumbnail_url = meta.get("thumbnail_url")

            raw_recipe = None
            if description and description.strip():
                raw_recipe = self.extract_recipe_from_caption(description)

            if raw_recipe is not None:
                logger.info("Recipe extracted from caption — skipping transcript")
            else:
                logger.info("Caption had no usable recipe — falling back to transcript")
                transcript = transcript_future.result().get("transcript", "")
                combined_text = self.combine_text(title, description, transcript)
                raw_recipe = self.extract_recipe_from_text(combined_text)

            normalized_title = self.normalize_recipe_title(title)
            ingredients = parse_ingredients(raw_recipe.get("ingredients", []))
            instructions = parse_instructions(raw_recipe.get("instructions", []))
            logger.info(f"Successfully processed recipe: {normalized_title}")

            return TikTokRecipeProcessorService(
                title=normalized_title,
                ingredients=ingredients,
                instructions=instructions,
                image=thumbnail_url,
                totalTime=raw_recipe.get("totalTime"),
                nutrition=parse_nutrition(raw_recipe, ingredients)
            )
        finally:
            # Don't block on a still-running transcription if the caption won.
            executor.shutdown(wait=False)