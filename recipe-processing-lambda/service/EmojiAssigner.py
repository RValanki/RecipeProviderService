import json
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


EMOJI_PROMPT = """
You are an expert at picking the single best food emoji for a cooking ingredient.
You will receive a JSON array of ingredient names. Return a JSON object mapping
EACH name (using the exact same string as the key) to the single most fitting emoji.

Rules:
- Exactly ONE emoji per ingredient — the closest, most specific real-world match.
- Prefer a specific food emoji over a generic one when a good match exists, e.g.
  "garlic" → 🧄, "egg" → 🥚, "soy sauce" → 🥫, "chilli" → 🌶️, "rice" → 🍚,
  "shrimp" → 🦐, "cheese" → 🧀, "avocado" → 🥑, "honey" → 🍯, "butter" → 🧈,
  "mushroom" → 🍄, "lemon" → 🍋, "noodles" → 🍜.
- If there is genuinely no sensible food emoji, use 🍽️.
- Keys MUST match the input names exactly. Return ONLY the JSON object, nothing else.
"""


def assign_emojis(api_key: str, names: list[str], model: str = "gpt-4o") -> dict:
    """Pick the best emoji for each ingredient name using a stronger model than the
    extraction step. Returns a {name: emoji} map. Returns {} on any failure so the
    caller can keep the extractor's original emojis."""
    # De-dupe while preserving order; drop blanks.
    unique = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if not unique:
        return {}

    try:
        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EMOJI_PROMPT},
                {"role": "user", "content": json.dumps(unique, ensure_ascii=False)},
            ],
        )
        content = (completion.choices[0].message.content or "").strip()
        # Be tolerant of code fences / stray prose — extract the JSON object.
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return {}
        data = json.loads(content[start:end + 1])
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
    except Exception as e:
        logger.error(f"Emoji assignment failed: {e}")
        return {}
