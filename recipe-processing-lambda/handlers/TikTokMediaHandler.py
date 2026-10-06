import json
from TikTokMediaProcessor import TikTokMediaProcessor
from ssrf import assert_safe_media_host, UnsafeURLError
import os

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")


def handler(event, context):
    try:
        url = event.get("url")

        if not url:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'url' in request"})
            }

        # Last line of defence before yt-dlp runs: the host must be TikTok's own
        # domain, regardless of how this Lambda was invoked.
        try:
            assert_safe_media_host(url, "tiktok")
        except UnsafeURLError as e:
            return {"statusCode": 400, "body": json.dumps({"error": f"unsafe_url: {e}"})}

        mode = event.get("mode", "full")
        processor = TikTokMediaProcessor(api_key=OPENAI_API_KEY)
        if mode == "metadata":
            media_payload = processor.process_metadata(url)
        elif mode == "transcribe":
            media_payload = processor.process_transcription(url)
        else:
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