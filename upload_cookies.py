"""
upload_cookies.py

Exports Instagram cookies from Chrome and uploads them to S3.

Usage:
    pip install browser-cookie3 boto3
    python upload_cookies.py
"""

import os
import boto3
import browser_cookie3

BUCKET = "recipe-instagram-cookies"
KEY = "cookies/instagram_cookies.txt"
LOCAL_PATH = "/tmp/instagram_cookies.txt"


def export_instagram_cookies(output_path: str):
    print("[1/3] Extracting Instagram cookies from Chrome...")

    cookies = browser_cookie3.chrome(domain_name="instagram.com")
    instagram_cookies = [c for c in cookies if "instagram.com" in c.domain]

    if not instagram_cookies:
        raise RuntimeError(
            "No Instagram cookies found. Make sure you're logged into Instagram in Chrome."
        )

    print(f"      Found {len(instagram_cookies)} cookies")

    with open(output_path, "w") as f:
        f.write("# Netscape HTTP Cookie File\n\n")
        for c in instagram_cookies:
            domain = c.domain if c.domain.startswith(".") else f".{c.domain}"
            secure = "TRUE" if c.secure else "FALSE"
            expires = int(c.expires) if c.expires else 0
            f.write(f"{domain}\tTRUE\t{c.path}\t{secure}\t{expires}\t{c.name}\t{c.value}\n")

    print(f"      Written to {output_path}")


def upload_to_s3(local_path: str):
    print(f"[2/3] Uploading to s3://{BUCKET}/{KEY}...")
    s3 = boto3.client("s3", region_name="ap-southeast-2")
    s3.upload_file(local_path, BUCKET, KEY)
    print("      Upload complete")


def verify(local_path: str):
    print("[3/3] Verifying...")
    s3 = boto3.client("s3", region_name="ap-southeast-2")
    resp = s3.head_object(Bucket=BUCKET, Key=KEY)
    print(f"      ✅ s3://{BUCKET}/{KEY} ({resp['ContentLength']} bytes)")
    print(f"      ✅ Local file: {os.path.getsize(local_path)} bytes")


if __name__ == "__main__":
    export_instagram_cookies(LOCAL_PATH)
    upload_to_s3(LOCAL_PATH)
    verify(LOCAL_PATH)
    print("\nDone! Run test.py to verify the Lambda works.")