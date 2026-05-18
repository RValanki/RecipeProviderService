import json
import os
from InstagramMediaProcessor import InstagramMediaProcessor

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
COOKIES_BUCKET = os.environ.get("COOKIES_BUCKET")
COOKIES_KEY = os.environ.get("COOKIES_KEY", "cookies/instagram_cookies.txt")


def handler(event, context):
    try:
        url = event.get("url")

        if not url:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'url' in request"})
            }

        # Temporary debug
        print(f"[debug] COOKIES_BUCKET={COOKIES_BUCKET}, COOKIES_KEY={COOKIES_KEY}")
        print(f"[debug] cookies file exists: {os.path.exists('/tmp/instagram_cookies.txt')}")

        processor = InstagramMediaProcessor(
            api_key=OPENAI_API_KEY,
            cookies_bucket=COOKIES_BUCKET,
            cookies_key=COOKIES_KEY
        )
        media_payload = processor.process(url)

        return {
            "statusCode": 200,
            "body": json.dumps(media_payload)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }