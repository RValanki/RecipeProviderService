from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Ingredient:
    name: str
    quantity: Optional[str] = None
    unit: Optional[str] = None
    emojiIcon: Optional[str] = None


@dataclass
class TikTokRecipeProcessorService:
    title: str
    ingredients: List[Ingredient]
    instructions: List[str]
    image: Optional[str] = None