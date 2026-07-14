# Force new build timestamp: 2026-07-13-12-45
import os
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pymongo import MongoClient
from core.config import PROJECT_NAME, API_PREFIX
from core.database import test_db_connection, upsert_manga_entry, IS_DB_ONLINE, check_db_online, manga_collection, get_canonical_slug, extract_chapter_number, infer_chapter_numbers
import core.database
from core.scheduler import start_scheduler
from scrapers.madara_base import scrape_madara_latest, scrape_madara_catalog, scrape_madara_details, scrape_madara_pages
from scrapers.meshmanga import scrape_meshmanga_latest, scrape_meshmanga_catalog, scrape_meshmanga_details, scrape_meshmanga_pages
import httpx
import logging

mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(mongo_uri)

logger = logging.getLogger("uvicorn")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify database connection
    await test_db_connection()
    start_scheduler()
    yield
    # Shutdown: clean up if needed

app = FastAPI(title=PROJECT_NAME, lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "Neo Manga API Server is running"}

@app.get(f"{API_PREFIX}/manga/latest")
async def get_latest_manga(site_url: str = Query(..., description="The base URL of the manga site to scrape")):
    """
    Get the latest manga updates from a specified website.
    """
    if not site_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL scheme. The URL must start with http:// or https://"
        )

    try:
        if "meshmanga.com" in site_url.lower():
            updates = await scrape_meshmanga_latest(site_url)
        else:
            updates = await scrape_madara_latest(site_url)
        
        # Ingest to MongoDB securely with Exception Shield
        if await check_db_online():
            for update in updates:
                try:
                    await upsert_manga_entry(update)
                except Exception as db_exc:
                    logger.error(f"Database ingestion failed for {update.get('title')}: {str(db_exc)}")
        else:
            logger.warning("MongoDB is offline. Skipping database ingestion loop entirely to prevent timeouts.")
                
        return {
            "status": "success",
            "site_url": site_url,
            "count": len(updates),
            "updates": updates
        }
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Request to target site timed out: {str(exc)}"
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Target site returned status error: {exc.response.status_code}"
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"HTTP connection error to target site: {str(exc)}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scrape latest updates: {str(exc)}"
        )


async def ingest_catalog_background(items: list):
    """
    Background task to ingest scraped catalog items into MongoDB.
    """
    if not items:
        return
    if await check_db_online():
        logger.info(f"[Background Task] Starting ingestion of {len(items)} items to MongoDB...")
        for item in items:
            try:
                await upsert_manga_entry(item)
            except Exception as db_exc:
                logger.error(f"[Background Task] Database ingestion failed for {item.get('title')}: {str(db_exc)}")
        logger.info("[Background Task] Ingestion completed.")
    else:
        logger.warning("[Background Task] MongoDB is offline. Skipping database ingestion loop.")


@app.get(f"{API_PREFIX}/manga/catalog")
async def get_manga_catalog(
    site_url: str = Query(..., description="The base URL of the manga site to scrape"),
    page: Optional[int] = Query(default=None),
    pages: Optional[int] = Query(default=None),
    background_tasks: BackgroundTasks = None
):
    """
    Scrape a specific page of the catalog, upsert each item into MongoDB, and return the list.
    Scraped updates are ingested asynchronously in the background.
    """
    if not site_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL scheme. The URL must start with http:// or https://"
        )

    final_page = 1
    if pages is not None:
        final_page = pages
    elif page is not None:
        final_page = page

    print(f"[Server] Client requested Catalog Page: {final_page} (Mapped from pages={pages} / page={page})")

    try:
        if "meshmanga.com" in site_url.lower():
            items = await scrape_meshmanga_catalog(site_url, page=final_page)
        else:
            items = await scrape_madara_catalog(site_url, page=final_page)
        
        # 2. ASYNCHRONOUS BACKGROUND WRITES
        if items and background_tasks:
            background_tasks.add_task(ingest_catalog_background, items)
            
        return {
            "status": "success",
            "site_url": site_url,
            "page": final_page,
            "pages_scraped": final_page,
            "count": len(items),
            "items": items
        }
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Request to target site timed out: {str(exc)}"
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Target site returned status error: {exc.response.status_code}"
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"HTTP connection error to target site: {str(exc)}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scrape catalog and ingest: {str(exc)}"
        )
@app.get(f"{API_PREFIX}/manga/details")
async def get_manga_details(
    manga_url: str = Query(..., description="The direct URL of the specific manga page to scrape details from")
):
    """
    Get inner details of a specific manga, including description, genres, and all chapters (ordered).
    """
    if not manga_url.startswith(("http://", "https://")):
        from urllib.parse import urljoin
        if "meshmanga.com" in manga_url.lower():
            manga_url = urljoin("https://meshmanga.com", manga_url)
        else:
            manga_url = urljoin("https://olympustaff.com", manga_url)

    if not manga_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL scheme. The URL must start with http:// or https://"
        )

    from urllib.parse import urlparse
    parsed = urlparse(manga_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        raise HTTPException(status_code=400, detail="Invalid manga URL: missing slug path")
    slug = path_parts[-1]
    
    try:
        canonical_slug = await get_canonical_slug(slug)
        manga_doc = None
        if await check_db_online():
            manga_doc = await manga_collection.find_one({"slug": canonical_slug})
            
        sources = {}
        if manga_doc and "sources" in manga_doc:
            for src_key, src_data in manga_doc["sources"].items():
                sources[src_key] = src_data["url"]
        
        # Ensure we always attempt to scrape both if database doesn't have them or is missing one
        if "meshmanga_com" not in sources:
            sources["meshmanga_com"] = f"https://meshmanga.com/series/{canonical_slug}/"
        if "olympustaff_com" not in sources:
            sources["olympustaff_com"] = f"https://olympustaff.com/series/{canonical_slug}/"
            
        async def fetch_source_details(source_key: str, url: str) -> Optional[dict]:
            loop = asyncio.get_running_loop()
            start_time = loop.time()
            try:
                if "meshmanga.com" in url.lower():
                    result = await asyncio.wait_for(scrape_meshmanga_details(url), timeout=5.0)
                else:
                    result = await asyncio.wait_for(scrape_madara_details(url), timeout=5.0)
                
                latency = loop.time() - start_time
                if result:
                    result["latency"] = latency
                    result["source_key"] = source_key
                return result
            except asyncio.TimeoutError:
                logger.warning(f"[API] Timeout (5s limit) fetching details for {source_key} at {url}")
                return None
            except Exception as e:
                logger.error(f"[API] Error fetching details for {source_key} at {url}: {str(e)}")
                return None

        # Scraping concurrently in parallel
        import asyncio
        tasks = [fetch_source_details(key, url) for key, url in sources.items()]
        scraped_results = await asyncio.gather(*tasks)
        
        successful_results = [r for r in scraped_results if r]
        if not successful_results:
            raise Exception("All target sources failed or timed out during details fetching")
            
        # Sort successful results by latency ascending (fastest first)
        successful_results.sort(key=lambda x: x.get("latency", 999.0))
        
        primary = successful_results[0]
        secondary = successful_results[1] if len(successful_results) > 1 else None
        
        # Populate details from primary
        description = primary.get("description") or ""
        genres = set(primary.get("genres") or [])
        
        def normalize_text(text: str) -> str:
            if not text:
                return ""
            text = text.lower().strip()
            text = re.sub(r'\b(?:الفصل|فصل|شابتر|chapter|ch|ep|episode)\b', '', text)
            text = re.sub(r'[أإآ]', 'ا', text)
            text = text.replace('ة', 'ه').replace('ى', 'ي')
            return "".join(re.findall(r'\w+', text))

        primary_chapters = primary.get("chapters", [])
        merged_chapters = {}
        
        # Process Primary Source
        for ch in primary_chapters:
            ch_title = ch.get("title", "")
            ch_url = ch.get("url", "")
            ch_num = extract_chapter_number(ch_title, ch_url)
            ch_payload = {
                "title": ch_title,
                "url": ch_url,
                "extracted_number": ch_num
            }
            key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
            merged_chapters[key] = ch_payload
            
        if secondary:
            if not description and secondary.get("description"):
                description = secondary["description"]
            if secondary.get("genres"):
                genres.update(secondary["genres"])
                
            secondary_chapters = secondary.get("chapters", [])
            for ch in secondary_chapters:
                ch_title = ch.get("title", "")
                ch_url = ch.get("url", "")
                ch_num = extract_chapter_number(ch_title, ch_url)
                ch_payload = {
                    "title": ch_title,
                    "url": ch_url,
                    "extracted_number": ch_num
                }
                key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
                if key not in merged_chapters:
                    merged_chapters[key] = ch_payload
                    
        # Sort descending (Newest to Oldest) by extracted number (stable sort)
        sorted_chapters = sorted(merged_chapters.values(), key=lambda x: x["extracted_number"], reverse=True)
        
        # Pass final merged results through infer_chapter_numbers to ensure strict monotonic ascending order
        final_chapters = infer_chapter_numbers(sorted_chapters)
        
        return {
            "status": "success",
            "manga_url": manga_url,
            "description": description,
            "genres": list(genres),
            "total_chapters": len(final_chapters),
            "chapters": final_chapters
        }
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Request to target site timed out: {str(exc)}"
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Target site returned status error: {exc.response.status_code}"
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"HTTP connection error to target site: {str(exc)}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scrape manga details: {str(exc)}"
        )


@app.get(f"{API_PREFIX}/chapters/pages")
async def get_chapter_pages(
    chapter_url: str = Query(..., description="The direct URL of the specific chapter page to scrape image URLs from")
):
    """
    Get the reading image URLs from a specific chapter page, and cache them in MongoDB.
    """
    if "meshmanga.com" in chapter_url and "/series/" in chapter_url:
        chapter_url = chapter_url.replace("meshmanga.com", "olympustaff.com")

    if not chapter_url.startswith(("http://", "https://")):
        from urllib.parse import urljoin
        if "meshmanga.com" in chapter_url.lower():
            chapter_url = urljoin("https://meshmanga.com", chapter_url)
        else:
            chapter_url = urljoin("https://olympustaff.com", chapter_url)

    if not chapter_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL scheme. The URL must start with http:// or https://"
        )

    try:
        import gc
        import asyncio
        from core.database import get_cached_chapter_pages, cache_chapter_pages

    # 1. Check cache first
        cached_pages = await get_cached_chapter_pages(chapter_url)
        if cached_pages:
            logger.info(f"[API] Serving cached chapter pages for: {chapter_url}")
            return {
                "status": "success",
                "chapter_url": chapter_url,
                "total_pages": len(cached_pages),
                "pages": cached_pages
            }

        # 2. Scrape raw page URLs
        from urllib.parse import urlparse
        chapter_url = chapter_url.strip()
        parsed_chapter = urlparse(chapter_url)
        chapter_domain = parsed_chapter.netloc.lower()

        try:
            if "meshmanga.com" in chapter_domain or "appswat.com" in chapter_domain:
                raw_pages = await asyncio.wait_for(scrape_meshmanga_pages(chapter_url), timeout=5.0)
            else:
                raw_pages = await asyncio.wait_for(scrape_madara_pages(chapter_url), timeout=5.0)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail="Request to target site timed out (5s limit)"
            )
            
        if not raw_pages:
            return {
                "status": "success",
                "chapter_url": chapter_url,
                "total_pages": 0,
                "pages": []
            }

        # 3. Save to cache directly
        if raw_pages:
            await cache_chapter_pages(chapter_url, raw_pages)

        # 4. Clean up temporary memory
        gc.collect()

        return {
            "status": "success",
            "chapter_url": chapter_url,
            "total_pages": len(raw_pages),
            "pages": raw_pages
        }
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail=f"Request to target site timed out: {str(exc)}"
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Target site returned status error: {exc.response.status_code}"
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"HTTP connection error to target site: {str(exc)}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to scrape chapter pages: {str(exc)}"
        )


@app.get("/api/cron-scrape")
async def cron_scrape():
    """
    Manually trigger the background scraping/traversal logic.
    """
    from core.scheduler import fetch_and_sync_latest_updates
    await fetch_and_sync_latest_updates()
    return {"status": "success", "message": "Scraping triggered successfully"}


