import os
import re
import logging
import asyncio
from datetime import datetime
from urllib.parse import urlparse
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("uvicorn")

MONGO_DETAILS = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Global database online flag (None = untested, True = online, False = offline)
IS_DB_ONLINE = None

# Initialize AsyncIOMotorClient with 1-second timeout
client = AsyncIOMotorClient(MONGO_DETAILS, serverSelectionTimeoutMS=1000)

# Define the database
database = client.neomanga_db

# Helper collection references
manga_collection = database.get_collection("manga_catalog")
chapters_collection = database.get_collection("chapter_pages")
slug_mappings_collection = database.get_collection("slug_mappings")

def generate_slug(title: str) -> str:
    """
    Generate a clean slug from the manga title (lowercase, trimmed, alphanumeric and hyphens only).
    Supports unicode letters to handle non-English manga names gracefully.
    """
    slug = title.lower().strip()
    # Replace spaces, hyphens, and underscores with a single hyphen
    slug = re.sub(r'[\s\-_]+', '-', slug)
    # Strip any character that is not a word character (alphanumeric) or hyphen
    slug = re.sub(r'[^\w\-]', '', slug)
    # Remove leading/trailing hyphens
    slug = slug.strip('-')
    return slug

async def test_db_connection():
    """
    Test MongoDB database connection by issuing a ping command.
    """
    global IS_DB_ONLINE
    try:
        # The admin database or the default database can be pinged
        await database.command("ping")
        logger.info("MongoDB Connection Test: SUCCESS. Bound to database 'neomanga_db'.")
        IS_DB_ONLINE = True
        return True
    except Exception as exc:
        logger.error(f"MongoDB Connection Test: FAILED. Check if MongoDB is running on localhost:27017. Error: {str(exc)}")
        IS_DB_ONLINE = False
        return False

async def check_db_online() -> bool:
    """
    Ensure the database connection is tested lazily.
    """
    global IS_DB_ONLINE
    if IS_DB_ONLINE is None:
        is_online = await test_db_connection()
        if is_online:
            await seed_slug_mappings_if_empty()
    return IS_DB_ONLINE

async def seed_slug_mappings_if_empty():
    """
    Seed the slug_mappings collection from a local JSON file if it is empty.
    """
    import json
    try:
        count = await slug_mappings_collection.count_documents({})
        if count == 0:
            json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "slug_mappings.json")
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    mappings = json.load(f)
                if mappings:
                    await slug_mappings_collection.insert_many(mappings)
                    logger.info(f"Successfully seeded {len(mappings)} slug mappings into MongoDB.")
            else:
                logger.warning(f"slug_mappings.json not found at {json_path}. Skipping seed.")
    except Exception as exc:
        logger.error(f"Failed to seed slug mappings: {str(exc)}")

async def get_canonical_slug(slug: str) -> str:
    """
    Looks up the slug mappings collection. Returns the canonical_slug if a match exists, 
    otherwise returns the input slug.
    """
    if not await check_db_online():
        return slug
    try:
        mapping = await slug_mappings_collection.find_one({"slug": slug})
        if mapping:
            return mapping.get("canonical_slug", slug)
    except Exception as exc:
        logger.error(f"Failed to lookup canonical slug for {slug}: {str(exc)}")
    return slug

def extract_chapter_number(title: str, url: str) -> float:
    """
    Extracts chapter number from title or URL.
    Prioritizes extraction from the URL path/slug first,
    then falls back to search patterns with keywords in the Title,
    and only uses the general numeric fallback as a last resort.
    """
    def to_float(val: str) -> float:
        try:
            return float(val)
        except ValueError:
            return 0.0

    url_str = url.lower() if url else ""
    title_str = title.lower() if title else ""

    # Priority 1: URL path/slug extraction (bypassed for MeshManga REST IDs)
    is_meshmanga = "meshmanga.com" in url_str or "appswat.com" in url_str
    if not is_meshmanga:
        # A. Check for chapter/ch prefix patterns
        match = re.search(r'(?:chapters|chapter|الفصل|ch)[/_ -]?([\d.]+)', url_str)
        if match:
            return to_float(match.group(1))

        # B. Check for raw trailing number patterns (e.g. /solo-resurrection/1 or /solo-resurrection/97/)
        match = re.search(r'/([\d.]+)/?$', url_str)
        if match:
            return to_float(match.group(1))

    # Priority 2: Title search patterns with keywords
    match = re.search(r'(?:فصل|الفصل|chapter|ch\.?)\s*([\d.]+)', title_str)
    if match:
        return to_float(match.group(1))

    # Priority 3: General numeric fallback in title (last resort)
    match = re.search(r'([\d.]+)', title_str)
    if match:
        return to_float(match.group(1))

    return 0.0

async def upsert_manga_entry(manga_data: dict) -> dict:
    """
    Upsert a manga entry into the MongoDB collection supporting a unified multi-source schema.
    Deduplicates manga profiles by using a clean slug generated from the title.
    """
    try:
        raw_slug = generate_slug(manga_data["title"])
        slug = await get_canonical_slug(raw_slug)
        
        # Parse the host name to act as a unique key for the source site
        parsed_url = urlparse(manga_data["url"])
        source_key = parsed_url.netloc.replace(".", "_")
        
        now_str = datetime.utcnow().isoformat()
        
        # Directly assign the raw, original target site cover URL
        thumbnail_url = manga_data.get("thumbnail", "")

        source_payload = {
            "url": manga_data["url"],
            "latest_chapter": manga_data["latest_chapter"],
            "updated_at": now_str
        }
        
        # Check if a document with that slug already exists
        existing_manga = await manga_collection.find_one({"slug": slug})
        
        if existing_manga:
            # Document exists: update the specific nested source field and updated_at
            update_payload = {
                f"sources.{source_key}": source_payload,
                "updated_at": now_str
            }
            # Fallback to copy thumbnail if the existing profile has none
            existing_thumb = existing_manga.get("thumbnail", "")
            if manga_data.get("thumbnail") and not existing_thumb:
                update_payload["thumbnail"] = manga_data["thumbnail"]
                
            result = await manga_collection.update_one(
                {"slug": slug},
                {"$set": update_payload}
            )
            return {
                "status": "updated",
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
                "upserted_id": None
            }
        else:
            # Document does not exist: create the complete initial document structure
            new_doc = {
                "title": manga_data["title"],
                "slug": slug,
                "thumbnail": manga_data.get("thumbnail", ""),
                "created_at": now_str,
                "updated_at": now_str,
                "sources": {
                    source_key: source_payload
                }
            }
            result = await manga_collection.insert_one(new_doc)
            return {
                "status": "inserted",
                "matched_count": 0,
                "modified_count": 0,
                "upserted_id": str(result.inserted_id)
            }
    except Exception as exc:
        logger.error(f"Failed to upsert manga entry for {manga_data.get('title')}: {str(exc)}")
        raise

async def get_cached_chapter_pages(chapter_url: str) -> list:
    """
    Retrieve cached chapter pages from MongoDB if online.
    """
    if not await check_db_online():
        return None
    try:
        doc = await chapters_collection.find_one({"chapter_url": chapter_url})
        if doc:
            return doc.get("pages")
    except Exception as exc:
        logger.error(f"Failed to fetch cached chapter pages for {chapter_url}: {str(exc)}")
    return None

async def cache_chapter_pages(chapter_url: str, pages: list):
    """
    Cache raw target chapter page URLs in MongoDB if online.
    """
    if not await check_db_online():
        return
    try:
        await chapters_collection.update_one(
            {"chapter_url": chapter_url},
            {
                "$set": {
                    "chapter_url": chapter_url,
                    "pages": pages,
                    "updated_at": datetime.utcnow().isoformat()
                }
            },
            upsert=True
        )
    except Exception as exc:
        logger.error(f"Failed to cache chapter pages for {chapter_url}: {str(exc)}")
