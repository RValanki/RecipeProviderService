"""
Firestore service for writing events to Firebase.
Uses google-cloud-firestore directly to avoid firebase-admin's heavy dependencies.
Authenticates via service account JSON stored in FIREBASE_SERVICE_ACCOUNT env var.
"""
import os
import json
import uuid
from datetime import datetime, timezone

from google.cloud import firestore
from google.oauth2 import service_account


_db = None

def _get_db():
    """Initialize Firestore client once per Lambda container lifecycle."""
    global _db
    if _db is None:
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        if not service_account_json:
            raise EnvironmentError("FIREBASE_SERVICE_ACCOUNT environment variable is not set")

        key = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            key,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        _db = firestore.Client(
            project=key["project_id"],
            credentials=credentials
        )
    return _db


def write_logged_meal_event(user_id: str, meal_data: dict) -> str:
    """
    Write a loggedMeal event to Firestore under users/{userId}/events.

    Args:
        user_id: Firebase user ID
        meal_data: The full API response dict (dishName, itemType, components, totalNutrition)

    Returns:
        The Firestore document ID of the created event
    """
    db = _get_db()

    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "type": "loggedMeal",
        "title": "Meal Ready",
        "message": f"{meal_data['dishName']} has been calculated",
        "priority": 1,
        "timestamp": datetime.now(timezone.utc),
        "readBy": [],
        "mealData": meal_data,
    }

    db.collection("users") \
      .document(user_id) \
      .collection("events") \
      .document(event_id) \
      .set(event)

    print(f"✅ loggedMeal event written for user {user_id}: {event_id}")
    return event_id


def write_recipe_ready_event(user_id: str, recipe_data: dict) -> str:
    """
    Write a recipeReady event to Firestore under users/{userId}/events.

    Args:
        user_id: Firebase user ID
        recipe_data: The full recipe response dict (title, image, ingredients, instructions)

    Returns:
        The Firestore document ID of the created event
    """
    db = _get_db()

    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "type": "recipeReady",
        "title": "Recipe Ready",
        "message": f"{recipe_data['title']} is ready to review",
        "priority": 1,
        "timestamp": datetime.now(timezone.utc),
        "readBy": [],
        "recipeData": recipe_data,
    }

    db.collection("users") \
      .document(user_id) \
      .collection("events") \
      .document(event_id) \
      .set(event)

    print(f"✅ recipeReady event written for user {user_id}: {event_id}")
    return event_id