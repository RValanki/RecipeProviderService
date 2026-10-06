import re
import json
import boto3
import logging
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI
from models import TikTokRecipeProcessorService
from TikTokRecipeProcessor import INGREDIENT_PROMPT, CAPTION_EXTRACT_PROMPT, parse_ingredients, parse_instructions, parse_nutrition

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class InstagramRecipeProcessor:

    def __init__(self, api_key: str, media_lambda_name: str):
        self.client = OpenAI(api_key=api_key)
        self.lambda_client = boto3.client("lambda")
        self.media_lambda_name = media_lambda_name

    def invoke_media_processor(self, url: str, mode: str = "full") -> dict:
        logger.info(f"Invoking Instagram media processor Lambda (mode={mode}) for URL: {url}")
        response = self.lambda_client.invoke(
            FunctionName=self.media_lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"url": url, "mode": mode})
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
You are given the title, caption, and spoken transcript of an Instagram cooking video.
Work out what the dish ACTUALLY is from all three sources together, then return its
clean, standard recipe name.
Rules:
- Do NOT blindly copy the caption or video title — creators often use vague, clickbait,
  or unrelated captions. Infer the real dish from the ingredients and steps described
  across the caption AND the transcript.
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
        logger.info(f"Processing Instagram Reel URL: {url}")

        # Kick off transcription (download + audio + Whisper — the slow part) in a
        # background thread right away, so it's ready IF the caption turns out to be
        # insufficient. On the happy path (caption is a complete recipe) we return
        # before it finishes and simply abandon it — that avoided Whisper call is
        # the whole point of trying the caption first.
        executor = ThreadPoolExecutor(max_workers=2)
        transcript_future = executor.submit(self.invoke_media_processor, url, "transcribe")
        try:
            meta = self.invoke_media_processor(url, "metadata")
            title = meta.get("title", "")
            description = meta.get("description", "")
            thumbnail_url = meta.get("thumbnail_url")

            # Caption-first: if the caption ALONE is a complete, unambiguous recipe
            # (dish name explicitly stated + full ingredients + clear steps), use it
            # and skip the transcript entirely. The prompt is strict and returns
            # {"recipe": null} on any uncertainty, so we only take this fast path
            # when the caption is genuinely self-sufficient. Skip the call outright
            # when there's no caption to read.
            caption_recipe = self.extract_recipe_from_caption(description) if description.strip() else None
            if caption_recipe:
                logger.info("Caption is a complete recipe — skipping audio transcript")
                ingredients = parse_ingredients(caption_recipe.get("ingredients", []))
                instructions = parse_instructions(caption_recipe.get("instructions", []))
                return TikTokRecipeProcessorService(
                    title=caption_recipe.get("title", ""),
                    ingredients=ingredients,
                    instructions=instructions,
                    image=thumbnail_url,
                    totalTime=caption_recipe.get("totalTime"),
                    nutrition=parse_nutrition(caption_recipe, ingredients)
                )

            # Caption wasn't enough — fall back to caption + audio transcript.
            logger.info("Caption insufficient — falling back to audio transcript")
            transcript = transcript_future.result().get("transcript", "")
            combined_text = self.combine_text(title, description, transcript)

            # Extraction and title-normalisation are independent (both read only the
            # already-fetched text, not each other's output), so run the two model
            # calls concurrently instead of serially — saves one round-trip.
            extract_future = executor.submit(self.extract_recipe_from_text, combined_text)
            title_future = executor.submit(self.normalize_recipe_title, title, description, transcript)
            raw_recipe = extract_future.result()
            normalized_title = title_future.result()

            ingredients = parse_ingredients(raw_recipe.get("ingredients", []))
            instructions = parse_instructions(raw_recipe.get("instructions", []))
            logger.info(f"Successfully processed Instagram recipe: {normalized_title}")

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