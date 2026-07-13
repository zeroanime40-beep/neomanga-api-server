import os
import httpx
import logging
import asyncio
import cloudinary
import cloudinary.uploader
from io import BytesIO

logger = logging.getLogger("uvicorn")

# Initialize Cloudinary configuration
# Vercel environment variables are CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

async def upload_image_to_cloudinary(image_bytes_or_url, folder_path: str) -> str:
    """
    Upload an image to Cloudinary.
    Accepts image bytes or a URL string.
    If a URL is provided, it downloads the image first using httpx to bypass hotlinking and Cloudflare protection,
    then uploads it to Cloudinary.
    Returns the secure URL of the uploaded image.
    """
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    if not cloud_name:
        logger.warning("Cloudinary environment variables are not configured. Returning original URL or raising.")
        if isinstance(image_bytes_or_url, str):
            return image_bytes_or_url
        raise ValueError("Cloudinary is not configured and input is not a URL string.")

    try:
        file_payload = None
        if isinstance(image_bytes_or_url, str) and image_bytes_or_url.startswith(("http://", "https://")):
            # Download image bytes first
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": image_bytes_or_url,
            }
            async with httpx.AsyncClient(headers=headers, timeout=20.0, follow_redirects=True) as client:
                response = await client.get(image_bytes_or_url)
                response.raise_for_status()
                # Wrap content in BytesIO for Cloudinary uploading
                file_payload = BytesIO(response.content)
        elif isinstance(image_bytes_or_url, bytes):
            file_payload = BytesIO(image_bytes_or_url)
        else:
            file_payload = image_bytes_or_url

        def _sync_upload():
            response = cloudinary.uploader.upload(
                file_payload,
                folder=folder_path
            )
            return response.get("secure_url")

        secure_url = await asyncio.to_thread(_sync_upload)
        
        # Clean up BytesIO memory
        if file_payload and hasattr(file_payload, "close"):
            file_payload.close()
            
        return secure_url
    except Exception as e:
        logger.error(f"Failed to upload image to Cloudinary under folder {folder_path}: {str(e)}")
        # Fallback to the original URL if we failed
        if isinstance(image_bytes_or_url, str):
            return image_bytes_or_url
        raise
