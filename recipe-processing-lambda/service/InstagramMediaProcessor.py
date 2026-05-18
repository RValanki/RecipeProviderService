import os
import uuid
import json
import subprocess
import boto3
from openai import OpenAI


class InstagramMediaProcessor:

    def __init__(
        self,
        api_key: str,
        cookies_bucket: str = None,
        cookies_key: str = "cookies/instagram_cookies.txt",
        cookies_path: str = "/tmp/instagram_cookies.txt"
    ):
        self.client = OpenAI(api_key=api_key)
        self.cookies_path = cookies_path

        if cookies_bucket:
            self._download_cookies_from_s3(cookies_bucket, cookies_key)

    # -----------------------------
    # 0️⃣ Download cookies from S3
    # -----------------------------
    def _download_cookies_from_s3(self, bucket: str, key: str):
        print(f"[cookies] Downloading cookies from s3://{bucket}/{key}")
        s3 = boto3.client("s3")
        s3.download_file(bucket, key, self.cookies_path)
        print(f"[cookies] Cookies saved to {self.cookies_path}")

    # -----------------------------
    # 1️⃣ Instagram metadata (title, description, thumbnail)
    # -----------------------------
    def get_instagram_metadata(self, url: str):
        result = subprocess.run(
            ["yt-dlp", "--cookies", self.cookies_path, "--dump-json", url],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp metadata failed: {result.stderr}")

        data = json.loads(result.stdout)
        title = data.get("title", "")
        description = data.get("description", "")
        thumbnails = data.get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else data.get("thumbnail")

        return title, description, thumbnail_url

    # -----------------------------
    # 2️⃣ Download Instagram Reel
    # -----------------------------
    def download_reel(self, url: str, unique_id: str) -> str:
        output_path = f"/tmp/video_{unique_id}.mp4"
        result = subprocess.run([
            "yt-dlp",
            "--cookies", self.cookies_path,
            "-o", output_path,
            url
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp download failed: {result.stderr}")

        if not os.path.exists(output_path):
            raise RuntimeError(f"yt-dlp completed but video file not found at {output_path}")

        return output_path

    # -----------------------------
    # 3️⃣ Extract audio
    # -----------------------------
    def extract_audio(self, video_file: str, unique_id: str) -> str:
        audio_path = f"/tmp/audio_{unique_id}.mp3"
        result = subprocess.run([
            "ffmpeg",
            "-y",
            "-i", video_file,
            "-vn",
            "-acodec", "mp3",
            audio_path
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")

        if not os.path.exists(audio_path):
            raise RuntimeError(f"ffmpeg completed but audio file not found at {audio_path}")

        return audio_path

    # -----------------------------
    # 4️⃣ Transcribe audio
    # -----------------------------
    def transcribe_audio(self, audio_file: str) -> str:
        with open(audio_file, "rb") as f:
            transcript = self.client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f
            )
        return transcript.text

    # -----------------------------
    # 5️⃣ Cleanup temp files
    # -----------------------------
    def cleanup(self, *files):
        for file in files:
            if file and os.path.exists(file):
                os.remove(file)
                print(f"[cleanup] Deleted {file}")

    # -----------------------------
    # Full pipeline
    # -----------------------------
    def process(self, url: str) -> dict:
        unique_id = uuid.uuid4().hex
        video_file = None
        audio_file = None

        try:
            title, description, thumbnail_url = self.get_instagram_metadata(url)
            video_file = self.download_reel(url, unique_id)
            audio_file = self.extract_audio(video_file, unique_id)
            transcript = self.transcribe_audio(audio_file)

            return {
                "title": title,
                "description": description,
                "transcript": transcript,
                "thumbnail_url": thumbnail_url
            }

        finally:
            self.cleanup(video_file, audio_file)