import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from pymongo import MongoClient
from core.config import PROJECT_NAME, API_PREFIX
from core.database import test_db_connection, upsert_manga_entry, IS_DB_ONLINE
import core.database
from core.scheduler import start_scheduler
from scrapers.madara_base import scrape_madara_latest, scrape_madara_catalog, scrape_madara_details, scrape_madara_pages
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
async def get_latest_manga(site_url: str = Query(..., description="The base URL of the Madara manga site to scrape")):
    """
    Get the latest manga updates from a specified Madara-based website.
    """
    # Simple validation on scheme
    if not site_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL scheme. The URL must start with http:// or https://"
        )

    try:
        updates = await scrape_madara_latest(site_url)
        
        # Ingest to MongoDB securely with Exception Shield
        if core.database.IS_DB_ONLINE:
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
        # Pass through status codes or return 502/504
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


@app.get(f"{API_PREFIX}/manga/catalog")
async def get_manga_catalog(
    site_url: str = Query(..., description="The base URL of the Madara manga site to scrape"),
    page: Optional[int] = Query(default=None),
    pages: Optional[int] = Query(default=None)
):
    """
    Scrape a specific page of the catalog from a given Madara-based site,
    upsert each item into MongoDB, and return the list.
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
        items = await scrape_madara_catalog(site_url, page=final_page)
        
        # Ingest to MongoDB securely with Exception Shield
        if core.database.IS_DB_ONLINE:
            for item in items:
                try:
                    await upsert_manga_entry(item)
                except Exception as db_exc:
                    logger.error(f"Database ingestion failed for {item.get('title')}: {str(db_exc)}")
        else:
            logger.warning("MongoDB is offline. Skipping database ingestion loop entirely to prevent timeouts.")
            
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
        manga_url = urljoin("https://olympustaff.com", manga_url)

    if not manga_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL scheme. The URL must start with http:// or https://"
        )

    try:
        details = await scrape_madara_details(manga_url)
        return {
            "status": "success",
            "manga_url": manga_url,
            **details
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
    Get the reading image URLs from a specific chapter page, uploading them to Cloudinary and caching in DB.
    """
    if not chapter_url.startswith(("http://", "https://")):
        from urllib.parse import urljoin
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
        from core.storage import upload_image_to_cloudinary

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
        raw_pages = await scrape_madara_pages(chapter_url)
        if not raw_pages:
            return {
                "status": "success",
                "chapter_url": chapter_url,
                "total_pages": 0,
                "pages": []
            }

        # 3. Upload them to Cloudinary concurrently
        tasks = [
            upload_image_to_cloudinary(page_url, "neomanga/chapters/")
            for page_url in raw_pages
        ]
        
        uploaded_pages = await asyncio.gather(*tasks)
        uploaded_pages = [p for p in uploaded_pages if p]

        # 4. Save to cache
        if uploaded_pages:
            await cache_chapter_pages(chapter_url, uploaded_pages)

        # 5. Clean up temporary memory
        gc.collect()

        return {
            "status": "success",
            "chapter_url": chapter_url,
            "total_pages": len(uploaded_pages),
            "pages": uploaded_pages
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


