import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import core.database
from core.database import IS_DB_ONLINE, upsert_manga_entry, check_db_online
from scrapers.madara_base import scrape_madara_latest

logger = logging.getLogger("uvicorn")

# Configure target sites for background scraping
TARGET_SITES = ["https://olympustaff.com/"]

async def fetch_and_sync_latest_updates():
    """
    Periodic task to fetch the latest manga updates from TARGET_SITES
    and sync them to the database if MongoDB is online.
    """
    # Dynamically check the database status flag
    if not await check_db_online():
        logger.warning("[Scheduler] MongoDB is offline. Skipping background sync task.")
        return

    logger.info("[Scheduler] Starting periodic background sync for target sites...")
    for site in TARGET_SITES:
        try:
            logger.info(f"[Scheduler] Scraped site: {site}")
            updates = await scrape_madara_latest(site)
            logger.info(f"[Scheduler] Found {len(updates)} updates from {site}. Ingesting...")
            
            for item in updates:
                try:
                    await upsert_manga_entry(item)
                except Exception as db_exc:
                    logger.error(f"[Scheduler] Failed to ingest {item.get('title')}: {str(db_exc)}")
            logger.info(f"[Scheduler] Ingestion completed for {site}.")
        except Exception as exc:
            logger.error(f"[Scheduler] Error syncing latest updates from {site}: {str(exc)}")
            
    logger.info("[Scheduler] Periodic background sync task completed.")

def start_scheduler():
    """
    Initializes and starts the AsyncIOScheduler.
    Runs the synchronization job at a regular interval (60 minutes).
    """
    logger.info("[Scheduler] Starting periodic background scheduler...")
    scheduler = AsyncIOScheduler()
    # Runs fetch_and_sync_latest_updates every 60 minutes
    scheduler.add_job(fetch_and_sync_latest_updates, "interval", minutes=60)
    scheduler.start()
    logger.info("[Scheduler] Periodic background scheduler started (Interval: 60 minutes).")
