# Configuration and settings for Neo Manga Centralized Backend Server
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

PROJECT_NAME = "Neo Manga Centralized Backend Server"
API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

class Settings:
    def __init__(self):
        self.CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
        self.CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
        self.CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")

settings = Settings()
