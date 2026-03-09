import sys
import os
import json
from unittest.mock import MagicMock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "service"))

from WebRecipeProcessor import WebRecipeProcessor
from models import Ingredient, TikTokRecipeProcessorService

SCHEMA_URL = "https://www.billyparisi.com/steak-frites-recipe-lemon-herb-butter/"
NO_SCHEMA_URL = "https://www.seriouseats.com/the-best-slow-cooked-italian-american-tomato-sauce-red-sauce-recipe"


# -----------------------------
# Helper to build processor
# -----------------------------
def build_processor():
    return WebRecipeProcessor(api_key=os.environ.get("OPENAI_API_KEY"))


# -----------------------------
# Test 1: Schema.org recipe extraction (live)
# -----------------------------
def test_schema_recipe():
    print("\n" + "=" * 50)
    print("test_schema_recipe")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SCHEMA_URL)

    assert isinstance(recipe, TikTokRecipeProcessorService)
    assert recipe.title is not None and len(recipe.title) > 0
    assert isinstance(recipe.ingredients, list)
    assert len(recipe.ingredients) > 0
    assert all(isinstance(i, Ingredient) for i in recipe.ingredients)
    assert isinstance(recipe.instructions, list)
    assert len(recipe.instructions) > 0

    print(json.dumps({
        "title": recipe.title,
        "image": recipe.image,
        "ingredients": [
            {"name": i.name, "quantity": i.quantity, "unit": i.unit, "emojiIcon": i.emojiIcon}
            for i in recipe.ingredients
        ],
        "instructions": recipe.instructions
    }, indent=2, ensure_ascii=False))

    print("\n✅ test_schema_recipe passed")


# -----------------------------
# Test 2: No step prefixes in instructions
# -----------------------------
def test_no_step_prefixes():
    print("\n" + "=" * 50)
    print("test_no_step_prefixes")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SCHEMA_URL)

    for instruction in recipe.instructions:
        assert not instruction.startswith("Step "), f"Found step prefix in: {instruction}"

    print(f"Instructions clean: {recipe.instructions}")
    print("\n✅ test_no_step_prefixes passed")


# -----------------------------
# Test 3: Invalid URL raises RuntimeError
# -----------------------------
def test_invalid_url():
    print("\n" + "=" * 50)
    print("test_invalid_url")
    print("=" * 50)

    processor = build_processor()

    try:
        processor.process("https://thiswebsitedoesnotexist12345.com/recipe")
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        print(f"Caught expected error: {e}")

    print("\n✅ test_invalid_url passed")


# -----------------------------
# Test 4: Ingredients are structured objects
# -----------------------------
def test_ingredients_are_structured():
    print("\n" + "=" * 50)
    print("test_ingredients_are_structured")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SCHEMA_URL)

    for ingredient in recipe.ingredients:
        assert isinstance(ingredient, Ingredient)
        assert ingredient.name is not None and len(ingredient.name) > 0

    print(f"Sample ingredient: {recipe.ingredients[0]}")
    print("\n✅ test_ingredients_are_structured passed")


# -----------------------------
# Test 5: All ingredients have emojiIcon
# -----------------------------
def test_emoji_icons():
    print("\n" + "=" * 50)
    print("test_emoji_icons")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SCHEMA_URL)

    for ingredient in recipe.ingredients:
        assert ingredient.emojiIcon is not None, f"Missing emojiIcon for: {ingredient.name}"
        print(f"{ingredient.emojiIcon} {ingredient.name}")

    print("\n✅ test_emoji_icons passed")


# -----------------------------
# Test 6: AI fallback (live - site with no Schema.org)
# -----------------------------
def test_ai_fallback():
    print("\n" + "=" * 50)
    print("test_ai_fallback")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(NO_SCHEMA_URL)

    assert isinstance(recipe, TikTokRecipeProcessorService)
    assert recipe.title is not None and len(recipe.title) > 0
    assert len(recipe.ingredients) > 0
    assert len(recipe.instructions) > 0

    print(json.dumps({
        "title": recipe.title,
        "image": recipe.image,
        "ingredients": [
            {"name": i.name, "quantity": i.quantity, "unit": i.unit, "emojiIcon": i.emojiIcon}
            for i in recipe.ingredients
        ],
        "instructions": recipe.instructions
    }, indent=2, ensure_ascii=False))

    print("\n✅ test_ai_fallback passed")


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    test_schema_recipe()
    test_no_step_prefixes()
    test_invalid_url()
    test_ingredients_are_structured()
    test_emoji_icons()
    # test_ai_fallback()  # uncomment to test AI fallback path