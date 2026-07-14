import os
import re
import logging
import asyncio
import time
from datetime import datetime
from urllib.parse import urlparse
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("uvicorn")

MONGO_DETAILS = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# Global database online flag (None = untested, True = online, False = offline)
IS_DB_ONLINE = None
LAST_DB_CHECK_TIME = 0.0
DB_RETRY_COOLDOWN = 30.0  # 30 seconds connection test retry window

# Initialize AsyncIOMotorClient with dynamic pooling and raised timeout limits
client = AsyncIOMotorClient(
    MONGO_DETAILS,
    serverSelectionTimeoutMS=3000,
    maxPoolSize=20,
    minPoolSize=0
)

# Define the database
database = client.neomanga_db

# Helper collection references
manga_collection = database.get_collection("manga_catalog")
chapters_collection = database.get_collection("chapter_pages")
slug_mappings_collection = database.get_collection("slug_mappings")

async def create_unique_indexes():
    """
    Create unique programmatic indexes on manga_catalog.slug and chapter_pages.chapter_url.
    Wrapped in try-except bounds to prevent startup crashes.
    """
    try:
        await manga_collection.create_index("slug", unique=True)
        logger.info("[Database] Unique index verified/created on 'manga_catalog.slug'")
    except Exception as exc:
        logger.warning(f"[Database] Failed to verify/create unique index on 'manga_catalog.slug': {str(exc)}")

    try:
        await chapters_collection.create_index("chapter_url", unique=True)
        logger.info("[Database] Unique index verified/created on 'chapter_pages.chapter_url'")
    except Exception as exc:
        logger.warning(f"[Database] Failed to verify/create unique index on 'chapter_pages.chapter_url': {str(exc)}")

async def test_db_connection():
    """
    Test MongoDB database connection by issuing a ping command and verifying indexes.
    """
    global IS_DB_ONLINE
    try:
        # The admin database or the default database can be pinged
        await database.command("ping")
        logger.info("MongoDB Connection Test: SUCCESS. Bound to database 'neomanga_db'.")
        IS_DB_ONLINE = True
        # Verify/create unique indexes on successful connection
        await create_unique_indexes()
        return True
    except Exception as exc:
        logger.error(f"MongoDB Connection Test: FAILED. Check if MongoDB is running on localhost:27017. Error: {str(exc)}")
        IS_DB_ONLINE = False
        return False

async def check_db_online() -> bool:
    """
    Ensure the database connection is tested lazily with a Retry Cooldown.
    """
    global IS_DB_ONLINE, LAST_DB_CHECK_TIME
    current_time = time.time()
    
    if IS_DB_ONLINE is None or (not IS_DB_ONLINE and (current_time - LAST_DB_CHECK_TIME) > DB_RETRY_COOLDOWN):
        logger.info("[Database] Lazily testing/retrying database connection...")
        LAST_DB_CHECK_TIME = current_time
        is_online = await test_db_connection()
        if is_online:
            await seed_slug_mappings_if_empty()
            
    return IS_DB_ONLINE

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

    return -1.0

def infer_chapter_numbers(chapters: list) -> list:
    """
    Infers missing chapter numbers (represented by -1.0) using lookahead/lookbehind context,
    enforcing a strictly monotonic ascending sequence of floats.
    Input format: list of dicts, in descending chronological order (newest first).
    Output format: list of dicts, in descending chronological order (newest first).
    """
    if not chapters:
        return []

    # 1. Reverse to ascending chronological order (oldest first)
    asc_ch = list(reversed(chapters))
    n = len(asc_ch)

    # Helper: read/write chapter float
    def get_num(idx: int) -> float:
        ch = asc_ch[idx]
        if "chapter_number" in ch:
            return ch["chapter_number"]
        ch_title = ch.get("title", "")
        ch_url = ch.get("url", "")
        val = extract_chapter_number(ch_title, ch_url)
        ch["chapter_number"] = val
        return val

    # Find first valid index (value != -1.0)
    first_valid_idx = -1
    for i in range(n):
        if get_num(i) != -1.0:
            first_valid_idx = i
            break

    # Scenario A: No valid chapter number exists in the entire list
    if first_valid_idx == -1:
        for i in range(n):
            asc_ch[i]["chapter_number"] = float(i + 1)
        return list(reversed(asc_ch))

    # Scenario B: Extrapolate backwards for any early -1.0 (prologues/specials)
    v = get_num(first_valid_idx)
    if first_valid_idx > 0:
        if v <= 0.0:
            delta = 1.0
        else:
            delta = v / (first_valid_idx + 1)
        for i in range(first_valid_idx):
            asc_ch[i]["chapter_number"] = v - (first_valid_idx - i) * delta

    # Scenario C: Forward monotonic enforcement
    last_valid_val = get_num(first_valid_idx)
    for i in range(first_valid_idx + 1, n):
        parsed_val = get_num(i)
        if parsed_val == -1.0:
            inferred = last_valid_val + 1.0
            asc_ch[i]["chapter_number"] = inferred
            last_valid_val = inferred
        else:
            if parsed_val <= last_valid_val:
                corrected = last_valid_val + 0.01
                asc_ch[i]["chapter_number"] = corrected
                last_valid_val = corrected
            else:
                last_valid_val = parsed_val

    return list(reversed(asc_ch))


async def upsert_manga_entry(manga_data: dict) -> dict:
    """
    Upsert a manga entry atomically into the MongoDB collection supporting a unified multi-source schema.
    Deduplicates manga profiles by using a clean slug generated from the title.
    """
    try:
        raw_slug = generate_slug(manga_data["title"])
        slug = await get_canonical_slug(raw_slug)
        
        # Parse the host name to act as a unique key for the source site
        url = manga_data["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        parsed_url = urlparse(url)
        source_key = parsed_url.netloc.replace(".", "_")
        
        now_str = datetime.utcnow().isoformat()
        thumbnail_url = manga_data.get("thumbnail", "")

        source_payload = {
            "url": url,
            "latest_chapter": manga_data.get("latest_chapter") or "",
            "updated_at": now_str
        }
        
        # Perform single atomic upsert operation
        result = await manga_collection.update_one(
            {"slug": slug},
            {
                "$set": {
                    f"sources.{source_key}": source_payload,
                    "updated_at": now_str
                },
                "$setOnInsert": {
                    "title": manga_data["title"],
                    "slug": slug,
                    "thumbnail": thumbnail_url,
                    "created_at": now_str
                }
            },
            upsert=True
        )

        if result.upserted_id is not None:
            return {
                "status": "inserted",
                "matched_count": 0,
                "modified_count": 0,
                "upserted_id": str(result.upserted_id)
            }
        else:
            return {
                "status": "updated",
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
                "upserted_id": None
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
