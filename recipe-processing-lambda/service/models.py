from dataclasses import dataclass
from typing import List, Optional


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