import sys
import os
import json

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "service"))

from TextRecipeProcessor import TextRecipeProcessor
from models import Ingredient, TikTokRecipeProcessorService

SAMPLE_TEXT = """
Classic Pancakes

Ingredients:
- 1 1/2 cups all-purpose flour
- 3 1/2 tsp baking powder
- 1 tsp salt
- 1 tbsp sugar
- 1 1/4 cups milk
- 1 egg
- 3 tbsp butter, melted

Instructions:
1. In a large bowl, sift together the flour, baking powder, salt, and sugar.
2. Make a well in the center and pour in the milk, egg, and melted butter; mix until smooth.
3. Heat a lightly oiled griddle or frying pan over medium-high heat.
4. Pour or scoop the batter onto the griddle, using approximately 1/4 cup for each pancake.
5. Brown on both sides and serve hot.
"""


# -----------------------------
# Helper to build processor
# -----------------------------
def build_processor():
    return TextRecipeProcessor(api_key=os.environ.get("OPENAI_API_KEY"))


# -----------------------------
# Test 1: Full pipeline
# -----------------------------
def test_full_pipeline():
    print("\n" + "=" * 50)
    print("test_full_pipeline")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SAMPLE_TEXT)

    assert isinstance(recipe, TikTokRecipeProcessorService)
    assert recipe.title is not None and len(recipe.title) > 0
    assert isinstance(recipe.ingredients, list)
    assert len(recipe.ingredients) > 0
    assert all(isinstance(i, Ingredient) for i in recipe.ingredients)
    assert isinstance(recipe.instructions, list)
    assert len(recipe.instructions) > 0
    assert recipe.image is None

    print(json.dumps({
        "title": recipe.title,
        "image": recipe.image,
        "ingredients": [
            {"name": i.name, "quantity": i.quantity, "unit": i.unit}
            for i in recipe.ingredients
        ],
        "instructions": recipe.instructions
    }, indent=2, ensure_ascii=False))

    print("\n✅ test_full_pipeline passed")


# -----------------------------
# Test 2: Ingredients are structured objects
# -----------------------------
def test_ingredients_are_structured():
    print("\n" + "=" * 50)
    print("test_ingredients_are_structured")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SAMPLE_TEXT)

    for ingredient in recipe.ingredients:
        assert isinstance(ingredient, Ingredient)
        assert ingredient.name is not None and len(ingredient.name) > 0

    print(f"Sample ingredient: {recipe.ingredients[0]}")
    print("\n✅ test_ingredients_are_structured passed")


# -----------------------------
# Test 3: No step prefixes in instructions
# -----------------------------
def test_no_step_prefixes():
    print("\n" + "=" * 50)
    print("test_no_step_prefixes")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SAMPLE_TEXT)

    for instruction in recipe.instructions:
        assert not instruction.startswith("Step "), f"Found step prefix in: {instruction}"

    print(f"Instructions clean: {recipe.instructions}")
    print("\n✅ test_no_step_prefixes passed")


# -----------------------------
# Test 4: Image is None
# -----------------------------
def test_image_is_none():
    print("\n" + "=" * 50)
    print("test_image_is_none")
    print("=" * 50)

    processor = build_processor()
    recipe = processor.process(SAMPLE_TEXT)

    assert recipe.image is None
    print("Image is None as expected")
    print("\n✅ test_image_is_none passed")


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    test_full_pipeline()
    test_ingredients_are_structured()
    test_no_step_prefixes()
    test_image_is_none()