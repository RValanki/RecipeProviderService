import os
import uuid
import json
import subprocess
import requests
from openai import OpenAI


class TikTokMediaProcessor:

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    # -----------------------------
    # 1️⃣ TikTok metadata (title, description)
    # -----------------------------
    def get_tiktok_metadata(self, url):
        result = subprocess.run(
            ["yt-dlp", "--dump-json", url],
            capture_output=True,
            text=True
        )
        data = json.loads(result.stdout)
        title = data.get("title", "")
        description = data.get("description", "")
        return title, description

    # -----------------------------
    # 2️⃣ Download TikTok video
    # -----------------------------
    def download_tiktok(self, url, unique_id: str):
        output_path = f"/tmp/video_{unique_id}.mp4"
        subprocess.run([
            "yt-dlp",
            "-o", output_path,
            url
        ])
        return output_path

    # -----------------------------
    # 3️⃣ Extract audio
    # -----------------------------
    def extract_audio(self, video_file: str, unique_id: str):
        audio_path = f"/tmp/audio_{unique_id}.mp3"
        subprocess.run([
            "ffmpeg",
            "-y",
            "-i", video_file,
            "-vn",
            "-acodec", "mp3",
            audio_path
        ])
        return audio_path

    # -----------------------------
    # 4️⃣ Transcribe audio
    # -----------------------------
    def transcribe_audio(self, audio_file):
        with open(audio_file, "rb") as f:
            transcript = self.client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f
            )
        return transcript.text

    # -----------------------------
    # 5️⃣ Get TikTok thumbnail via oEmbed API
    # -----------------------------
    def get_tiktok_display_thumbnail(self, url):
        try:
            resp = requests.get(
                "https://www.tiktok.com/oembed",
                params={"url": url},
                timeout=10
            )
            resp.raise_for_status()
            return resp.json().get("thumbnail_url")
        except Exception as e:
            print(f"[thumbnail] oEmbed failed: {e}")
            return None

    # -----------------------------
    # 6️⃣ Cleanup temp files
    # -----------------------------
    def cleanup(self, *files):
        for file in files:
            if file and os.path.exists(file):
                os.remove(file)
                print(f"[cleanup] Deleted {file}")

    # -----------------------------
    # Metadata only — fast path (no download/transcription)
    # -----------------------------
    def process_metadata(self, url: str) -> dict:
        title, description = self.get_tiktok_metadata(url)
        thumbnail_url = self.get_tiktok_display_thumbnail(url)
        return {
            "title": title,
            "description": description,
            "thumbnail_url": thumbnail_url
        }

    # -----------------------------
    # Transcription only — slow path (download + audio + transcribe)
    # -----------------------------
    def process_transcription(self, url: str) -> dict:
        unique_id = uuid.uuid4().hex
        video_file = None
        audio_file = None

        try:
            video_file = self.download_tiktok(url, unique_id)
            audio_file = self.extract_audio(video_file, unique_id)
            transcript = self.transcribe_audio(audio_file)
            return {"transcript": transcript}

        finally:
            self.cleanup(video_file, audio_file)

    # -----------------------------
    # Full pipeline — metadata + transcript (backward compatible)
    # -----------------------------
    def process(self, url: str) -> dict:
        return {**self.process_metadata(url), **self.process_transcription(url)}