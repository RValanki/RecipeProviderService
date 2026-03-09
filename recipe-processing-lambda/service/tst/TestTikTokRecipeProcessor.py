import sys
import os
import json
from unittest.mock import MagicMock

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "service"))

from TikTokRecipeProcessor import TikTokRecipeProcessor
from models import Ingredient, TikTokRecipeProcessorService

TIKTOK_URL = "https://vt.tiktok.com/ZSuYJaYNT/"
MEDIA_LAMBDA_NAME = "RecipeStack-TikTokMediaProcessor1B912639-yYkJJuHDnPph"


# -----------------------------
# Helper to build processor
# -----------------------------
def build_processor():
    return TikTokRecipeProcessor(
        api_key=os.environ.get("OPENAI_API_KEY"),
        media_lambda_name=MEDIA_LAMBDA_NAME
    )


# -----------------------------
# Test 1: Full pipeline (hits real Lambda + OpenAI)
# -----------------------------
def test_full_pipeline():
    print("\n" + "=" * 50)
    print("test_full_pipeline")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(TIKTOK_URL)

    assert isinstance(recipe, TikTokRecipeProcessorService)
    assert recipe.title is not None and len(recipe.title) > 0
    assert isinstance(recipe.ingredients, list)
    assert len(recipe.ingredients) > 0
    assert all(isinstance(i, Ingredient) for i in recipe.ingredients)
    assert isinstance(recipe.instructions, list)
    assert len(recipe.instructions) > 0
    assert recipe.image is not None

    for ingredient in recipe.ingredients:
        assert ingredient.emojiIcon is not None, f"Missing emojiIcon for: {ingredient.name}"

    print(json.dumps({
        "title": recipe.title,
        "image": recipe.image,
        "ingredients": [
            {"name": i.name, "quantity": i.quantity, "unit": i.unit, "emojiIcon": i.emojiIcon}
            for i in recipe.ingredients
        ],
        "instructions": recipe.instructions
    }, indent=2, ensure_ascii=False))

    print("\n✅ test_full_pipeline passed")


# -----------------------------
# Test 2: Lambda invocation error handling
# -----------------------------
def test_lambda_function_error():
    print("\n" + "=" * 50)
    print("test_lambda_function_error")
    print("=" * 50)

    processor = build_processor()

    mock_response = {
        "FunctionError": "Unhandled",
        "Payload": MagicMock(read=lambda: json.dumps({
            "errorMessage": "Task timed out"
        }).encode())
    }
    processor.lambda_client.invoke = MagicMock(return_value=mock_response)

    try:
        processor.process(TIKTOK_URL)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "Task timed out" in str(e)
        print(f"Caught expected error: {e}")

    print("\n✅ test_lambda_function_error passed")


# -----------------------------
# Test 3: Lambda returns non-200 status
# -----------------------------
def test_lambda_non_200_status():
    print("\n" + "=" * 50)
    print("test_lambda_non_200_status")
    print("=" * 50)

    processor = build_processor()

    mock_response = {
        "FunctionError": None,
        "Payload": MagicMock(read=lambda: json.dumps({
            "statusCode": 400,
            "body": json.dumps({"error": "Missing 'url' in request"})
        }).encode())
    }
    processor.lambda_client.invoke = MagicMock(return_value=mock_response)

    try:
        processor.process(TIKTOK_URL)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "Missing 'url' in request" in str(e)
        print(f"Caught expected error: {e}")

    print("\n✅ test_lambda_non_200_status passed")


# -----------------------------
# Test 4: No step prefixes in instructions
# -----------------------------
def test_no_step_prefixes():
    print("\n" + "=" * 50)
    print("test_no_step_prefixes")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(TIKTOK_URL)

    for instruction in recipe.instructions:
        assert not instruction.startswith("Step "), f"Found step prefix in: {instruction}"

    print(f"Instructions clean: {recipe.instructions}")
    print("\n✅ test_no_step_prefixes passed")


# -----------------------------
# Test 5: All ingredients have emojiIcon
# -----------------------------
def test_emoji_icons():
    print("\n" + "=" * 50)
    print("test_emoji_icons")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(TIKTOK_URL)

    for ingredient in recipe.ingredients:
        assert ingredient.emojiIcon is not None, f"Missing emojiIcon for: {ingredient.name}"
        print(f"{ingredient.emojiIcon} {ingredient.name}")

    print("\n✅ test_emoji_icons passed")


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    test_full_pipeline()
    test_lambda_function_error()
    test_lambda_non_200_status()
    test_no_step_prefixes()
    test_emoji_icons()