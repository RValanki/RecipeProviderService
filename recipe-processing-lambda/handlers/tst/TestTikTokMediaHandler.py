import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "service"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

import json
from TikTokMediaHandler import handler


def test_valid_url():
    event = {
        "url": "https://vt.tiktok.com/ZSuYJaYNT/"
    }

    response = handler(event, context=None)

    print("Status Code:", response["statusCode"])
    body = json.loads(response["body"])
    print(json.dumps(body, indent=2, ensure_ascii=False))

    assert response["statusCode"] == 200
    assert "title" in body
    assert "description" in body
    assert "transcript" in body
    assert "thumbnail_url" in body
    print("\n✅ test_valid_url passed")


def test_missing_url():
    event = {}

    response = handler(event, context=None)

    print("Status Code:", response["statusCode"])
    body = json.loads(response["body"])
    print(json.dumps(body, indent=2, ensure_ascii=False))

    assert response["statusCode"] == 400
    assert "error" in body
    print("\n✅ test_missing_url passed")


if __name__ == "__main__":
    print("=" * 50)
    print("Running test_missing_url")
    print("=" * 50)
    test_missing_url()

    print("\n" + "=" * 50)
    print("Running test_valid_url")
    print("=" * 50)
    test_valid_url()