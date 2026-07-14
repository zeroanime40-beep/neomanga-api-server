# Force new build timestamp: 2026-07-14-13-30
import os
import re
import asyncio
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
def get_slug_candidates(canonical_slug: str, current_slug: str = None) -> list[str]:
    """
    Generate standard candidates for slug variations.
    """
    candidates = []
    if current_slug and current_slug not in candidates:
        candidates.append(current_slug)
    if canonical_slug not in candidates:
        candidates.append(canonical_slug)
        
    base_slug = canonical_slug
    for suffix in ["-manga", "-arabic"]:
        if base_slug.endswith(suffix):
            base_slug = base_slug[:-len(suffix)]
            break
            
    for cand in [base_slug, f"{base_slug}-manga", f"{base_slug}-arabic"]:
        if cand not in candidates:
            candidates.append(cand)
    return candidates


async def fetch_source_details_with_fallback(source_key: str, canonical_slug: str, initial_url: str) -> Optional[dict]:
    """
    Resiliently fetch target source details with sequential candidate URL testing.
    """
    from urllib.parse import urlparse
    parsed = urlparse(initial_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    current_slug = path_parts[-1] if path_parts else canonical_slug
    
    candidates = get_slug_candidates(canonical_slug, current_slug)
    logger.info(f"[Fallback Resolver] Slug candidates for {source_key}: {candidates}")
    
    last_exception = None
    for cand_slug in candidates:
        if source_key == "meshmanga_com":
            url = f"https://meshmanga.com/series/{cand_slug}/"
        else:
            url = f"https://olympustaff.com/series/{cand_slug}/"
            
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        try:
            logger.info(f"[Fallback Resolver] Attempting fetch for {source_key} with URL: {url}")
            if source_key == "meshmanga_com":
                result = await asyncio.wait_for(scrape_meshmanga_details(url), timeout=5.0)
            else:
                result = await asyncio.wait_for(scrape_madara_details(url), timeout=5.0)
                
            latency = loop.time() - start_time
            if result:
                result["latency"] = latency
                result["source_key"] = source_key
                result["resolved_url"] = url
                logger.info(f"[Fallback Resolver] Success for {source_key} at {url} in {latency:.2f}s")
                return result
        except asyncio.TimeoutError:
            logger.warning(f"[Fallback Resolver] Timeout fetching candidate {url} for {source_key}")
            last_exception = asyncio.TimeoutError("Timeout (5s limit)")
            break  # Break on timeout to prevent excessive delay for the user
        except Exception as e:
            logger.warning(f"[Fallback Resolver] Failed candidate {url} for {source_key}: {str(e)}")
            last_exception = e
            continue
            
    if last_exception:
        logger.error(f"[Fallback Resolver] All candidates failed for {source_key}. Last error: {str(last_exception)}")
    return None


async def heal_manga_details_background(canonical_slug: str, manga_url: str, sources: dict, existing_details: Optional[dict]):
    """
    Asynchronously scrape sources, merge chapters non-destructively, and update MongoDB details.
    """
    if not await check_db_online():
        logger.warning("[Background Heal] MongoDB is offline. Skipping background healing.")
        return
        
    logger.info(f"[Background Heal] Starting background healing for: {canonical_slug}")
    
    tasks = [fetch_source_details_with_fallback(key, canonical_slug, url) for key, url in sources.items()]
    scraped_results = await asyncio.gather(*tasks)
    successful_results = [r for r in scraped_results if r]
    
    if not successful_results:
        logger.error(f"[Background Heal] All sources failed during background healing for {canonical_slug}")
        return
        
    successful_results.sort(key=lambda x: x.get("latency", 999.0))
    primary = successful_results[0]
    secondary = successful_results[1] if len(successful_results) > 1 else None
    
    description = primary.get("description") or ""
    genres = set(primary.get("genres") or [])
    
    if secondary:
        if not description and secondary.get("description"):
            description = secondary["description"]
        if secondary.get("genres"):
            genres.update(secondary["genres"])
            
    if existing_details:
        if not description and existing_details.get("description"):
            description = existing_details["description"]
        if existing_details.get("genres"):
            genres.update(existing_details["genres"])
            
    merged_chapters = {}
    
    def normalize_text(text) -> str:
        if not isinstance(text, str):
            return ""
        text = text.lower().strip()
        text = re.sub(r'\b(?:الفصل|فصل|شابتر|chapter|ch|ep|episode)\b', '', text)
        text = re.sub(r'[أإآ]', 'ا', text)
        text = text.replace('ة', 'ه').replace('ى', 'ي')
        return "".join(re.findall(r'\w+', text))

    # 1. Cache chapters (lowest priority)
    if existing_details and "chapters" in existing_details:
        for ch in existing_details["chapters"]:
            try:
                ch_title = str(ch.get("title") or "")
                ch_url = str(ch.get("url") or "")
                ch_num = ch.get("extracted_number")
                if ch_num is None:
                    ch_num = ch.get("chapter_number")
                if ch_num is None:
                    ch_num = extract_chapter_number(ch_title, ch_url)
                try:
                    ch_num = float(ch_num)
                except (TypeError, ValueError):
                    ch_num = -1.0
                key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
                merged_chapters[key] = {
                    "title": ch_title,
                    "url": ch_url,
                    "chapter_number": ch_num,
                    "extracted_number": ch_num
                }
            except Exception:
                continue

    # 2. Secondary source chapters (medium priority)
    if secondary:
        for ch in secondary.get("chapters", []):
            try:
                ch_title = str(ch.get("title") or "")
                ch_url = str(ch.get("url") or "")
                ch_num = extract_chapter_number(ch_title, ch_url)
                try:
                    ch_num = float(ch_num)
                except (TypeError, ValueError):
                    ch_num = -1.0
                key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
                merged_chapters[key] = {
                    "title": ch_title,
                    "url": ch_url,
                    "chapter_number": ch_num,
                    "extracted_number": ch_num
                }
            except Exception:
                continue

    # 3. Primary source chapters (highest priority)
    for ch in primary.get("chapters", []):
        try:
            ch_title = str(ch.get("title") or "")
            ch_url = str(ch.get("url") or "")
            ch_num = extract_chapter_number(ch_title, ch_url)
            try:
                ch_num = float(ch_num)
            except (TypeError, ValueError):
                ch_num = -1.0
            key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
            merged_chapters[key] = {
                "title": ch_title,
                "url": ch_url,
                "chapter_number": ch_num,
                "extracted_number": ch_num
            }
        except Exception:
            continue

    sorted_chapters = sorted(merged_chapters.values(), key=lambda x: x["extracted_number"], reverse=True)
    final_chapters = infer_chapter_numbers(sorted_chapters)
    
    # Ensure that final_chapters contain both keys after inference too
    for ch in final_chapters:
        if "extracted_number" not in ch:
            ch["extracted_number"] = ch.get("chapter_number", -1.0)
        elif "chapter_number" not in ch:
            ch["chapter_number"] = ch.get("extracted_number", -1.0)
            
    now_str = datetime.utcnow().isoformat()
    cached_sources = [r["source_key"] for r in successful_results]
    
    details_payload = {
        "description": description,
        "genres": list(genres),
        "total_chapters": len(final_chapters),
        "chapters": final_chapters,
        "last_cached_at": now_str,
        "cached_sources": cached_sources
    }
    
    update_payload = {
        "details": details_payload,
        "updated_at": now_str
    }
    
    for r in successful_results:
        src_key = r["source_key"]
        resolved_url = r["resolved_url"]
        update_payload[f"sources.{src_key}.url"] = resolved_url
        update_payload[f"sources.{src_key}.updated_at"] = now_str
        
    try:
        await manga_collection.update_one(
            {"slug": canonical_slug},
            {"$set": update_payload}
        )
        logger.info(f"[Background Heal] Cache updated for {canonical_slug} (Sources: {cached_sources})")
    except Exception as exc:
        logger.error(f"[Background Heal] Failed to save cache: {str(exc)}")


@app.get(f"{API_PREFIX}/manga/details")
async def get_manga_details(
    manga_url: str = Query(..., description="The direct URL of the specific manga page to scrape details from"),
    background_tasks: BackgroundTasks = None
):
    """
    Get inner details of a specific manga, including description, genres, and all chapters (ordered).
    Implements Stale-While-Revalidate caching.
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
            
        cached_details = manga_doc.get("details") if manga_doc else None
        
        # Determine Cache Freshness (TTL = 2 hours)
        DETAILS_CACHE_TTL = 7200
        is_cache_fresh = False
        if cached_details and "last_cached_at" in cached_details:
            try:
                cached_at = datetime.fromisoformat(cached_details["last_cached_at"])
                age = (datetime.utcnow() - cached_at).total_seconds()
                if age < DETAILS_CACHE_TTL:
                    is_cache_fresh = True
            except Exception:
                pass
                
        # Retrieve candidate URLs
        sources = {}
        if manga_doc and "sources" in manga_doc:
            for src_key, src_data in manga_doc["sources"].items():
                sources[src_key] = src_data["url"]
        
        if "meshmanga_com" not in sources:
            sources["meshmanga_com"] = f"https://meshmanga.com/series/{canonical_slug}/"
        if "olympustaff_com" not in sources:
            sources["olympustaff_com"] = f"https://olympustaff.com/series/{canonical_slug}/"
            
        # Case A: Cache is Fresh -> Serve immediately
        if is_cache_fresh and cached_details:
            logger.info(f"[API] Serving fresh cached details for {canonical_slug}")
            return {
                "status": "success",
                "manga_url": manga_url,
                "description": cached_details.get("description") or "",
                "genres": cached_details.get("genres") or [],
                "total_chapters": cached_details.get("total_chapters") or 0,
                "chapters": cached_details.get("chapters") or []
            }
            
        # Case B: Cache Stale -> Serve immediately, heal in background
        if cached_details:
            logger.info(f"[API] Serving stale cached details for {canonical_slug}. Healing in background.")
            if background_tasks:
                background_tasks.add_task(
                    heal_manga_details_background,
                    canonical_slug,
                    manga_url,
                    sources,
                    cached_details
                )
            return {
                "status": "success",
                "manga_url": manga_url,
                "description": cached_details.get("description") or "",
                "genres": cached_details.get("genres") or [],
                "total_chapters": cached_details.get("total_chapters") or 0,
                "chapters": cached_details.get("chapters") or []
            }
            
        # Case C: No Cache -> Synchronous Live Fetch
        logger.info(f"[API] No cache found for {canonical_slug}. Fetching synchronously.")
        
        tasks = [fetch_source_details_with_fallback(key, canonical_slug, url) for key, url in sources.items()]
        scraped_results = await asyncio.gather(*tasks)
        successful_results = [r for r in scraped_results if r]
        
        if not successful_results:
            raise Exception("All target sources failed or timed out during details fetching")
            
        successful_results.sort(key=lambda x: x.get("latency", 999.0))
        primary = successful_results[0]
        secondary = successful_results[1] if len(successful_results) > 1 else None
        
        description = primary.get("description") or ""
        genres = set(primary.get("genres") or [])
        
        if secondary:
            if not description and secondary.get("description"):
                description = secondary["description"]
            if secondary.get("genres"):
                genres.update(secondary["genres"])
                
        merged_chapters = {}
        
        def normalize_text(text) -> str:
            if not isinstance(text, str):
                return ""
            text = text.lower().strip()
            text = re.sub(r'\b(?:الفصل|فصل|شابتر|chapter|ch|ep|episode)\b', '', text)
            text = re.sub(r'[أإآ]', 'ا', text)
            text = text.replace('ة', 'ه').replace('ى', 'ي')
            return "".join(re.findall(r'\w+', text))

        if secondary:
            for ch in secondary.get("chapters", []):
                try:
                    if not isinstance(ch, dict):
                        continue
                    ch_title = str(ch.get("title") or "")
                    ch_url = str(ch.get("url") or "")
                    ch_num = extract_chapter_number(ch_title, ch_url)
                    try:
                        ch_num = float(ch_num)
                    except (TypeError, ValueError):
                        ch_num = -1.0
                    key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
                    merged_chapters[key] = {
                        "title": ch_title,
                        "url": ch_url,
                        "chapter_number": ch_num,
                        "extracted_number": ch_num
                    }
                except Exception as loop_exc:
                    logger.warning(f"[API] Skipping corrupted secondary chapter item: {str(loop_exc)}")
                    continue
                    
        for ch in primary.get("chapters", []):
            try:
                if not isinstance(ch, dict):
                    continue
                ch_title = str(ch.get("title") or "")
                ch_url = str(ch.get("url") or "")
                ch_num = extract_chapter_number(ch_title, ch_url)
                try:
                    ch_num = float(ch_num)
                except (TypeError, ValueError):
                    ch_num = -1.0
                key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
                merged_chapters[key] = {
                    "title": ch_title,
                    "url": ch_url,
                    "chapter_number": ch_num,
                    "extracted_number": ch_num
                }
            except Exception as loop_exc:
                logger.warning(f"[API] Skipping corrupted primary chapter item: {str(loop_exc)}")
                continue
                
        sorted_chapters = sorted(merged_chapters.values(), key=lambda x: x["extracted_number"], reverse=True)
        final_chapters = infer_chapter_numbers(sorted_chapters)
        
        # Ensure that final_chapters contain both keys after inference too
        for ch in final_chapters:
            if "extracted_number" not in ch:
                ch["extracted_number"] = ch.get("chapter_number", -1.0)
            elif "chapter_number" not in ch:
                ch["chapter_number"] = ch.get("extracted_number", -1.0)
                
        now_str = datetime.utcnow().isoformat()
        cached_sources = [r["source_key"] for r in successful_results]
        
        details_payload = {
            "description": description,
            "genres": list(genres),
            "total_chapters": len(final_chapters),
            "chapters": final_chapters,
            "last_cached_at": now_str,
            "cached_sources": cached_sources
        }
        
        update_payload = {
            "details": details_payload,
            "updated_at": now_str
        }
        
        for r in successful_results:
            src_key = r["source_key"]
            resolved_url = r["resolved_url"]
            update_payload[f"sources.{src_key}.url"] = resolved_url
            update_payload[f"sources.{src_key}.updated_at"] = now_str
            
        if await check_db_online():
            try:
                await manga_collection.update_one(
                    {"slug": canonical_slug},
                    {"$set": update_payload}
                )
            except Exception as db_exc:
                logger.error(f"[API] Failed to save cache synchronously: {str(db_exc)}")
                
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


