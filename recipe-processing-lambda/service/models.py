import re
from dataclasses import dataclass
from typing import List, Optional


def normalize_recipe_title(title: str) -> str:
    """Recipe titles sometimes arrive in ALL CAPS (scraped pages, clickbait
    captions). Convert a fully-uppercase title to Title Case (first letter of
    each word); leave mixed-case titles — which may hold intentional acronyms
    like "BBQ" — untouched. Apostrophes stay inside their word so "GRANDMA'S"
    becomes "Grandma's" (not "Grandma'S"), matching the client."""
    if not title:
        return title
    letters = [c for c in title if c.isalpha()]
    if not (letters and all(c.isupper() for c in letters)):
        return title
    return re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)*", lambda m: m.group(0).capitalize(), title)


@dataclass
class Ingredient:
    name: str
    emoji: str
    quantity: Optional[float] = None
    unit: Optional[str] = None
    totalGram: Optional[float] = None
    gramPerUnit: Optional[float] = None
    matchID: Optional[str] = None


@dataclass
class NutritionValues:
    """A macro breakdown. Calories in kcal; protein, fat and carbs in grams."""
    calories: float
    protein: float
    fat: float
    carbs: float


@dataclass
class Nutrition:
    """Recipe nutrition with both whole-recipe and per-serving views.

    `total` is the sum across all ingredients. `perServing` is `total` divided by
    `servings` (computed, so it always reconciles). `servingSize` is a short
    logical-count description of ONE serving (e.g. "2 tacos", "1 bowl") and
    `servingSizeGram` is the approximate weight of one serving in grams."""
    servings: int
    servingSize: str
    servingSizeGram: float
    total: NutritionValues
    perServing: NutritionValues


@dataclass
class Instruction:
    """A single recipe step: the text, the ingredients used in that step
    (None when the step adds none), and an optional timer in minutes
    (the upper bound of any duration the step mentions; None if no time)."""
    text: str
    ingredients: Optional[List[Ingredient]] = None
    timer: Optional[int] = None


@dataclass
class TikTokRecipeProcessorService:
    title: str
    ingredients: List[Ingredient]
    instructions: List[Instruction]
    image: Optional[str] = None
    totalTime: Optional[int] = None
    nutrition: Optional[Nutrition] = None

    def __post_init__(self):
        # Centrally normalise the title for every processor path (text, web,
        # Instagram, TikTok) so an ALL-CAPS title never reaches the client.
        self.title = normalize_recipe_title(self.title)