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
class TikTokRecipeProcessorService:
    title: str
    ingredients: List[Ingredient]
    instructions: List[str]
    image: Optional[str] = None