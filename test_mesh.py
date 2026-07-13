import asyncio
import sys
import os

# Ensure current directory is in search path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from scrapers.meshmanga import scrape_meshmanga_catalog, scrape_meshmanga_latest, scrape_meshmanga_details, scrape_meshmanga_pages

async def test():
    print("----------------------------------------")
    print("Testing scrape_meshmanga_catalog page 1...")
    try:
        items = await scrape_meshmanga_catalog("https://meshmanga.com", 1)
        print(f"SUCCESS. Catalog items count: {len(items)}")
        for item in items[:3]:
            print(f" - {item}")
    except Exception as e:
        print(f"FAILED. Catalog scrape error: {e}")
        import traceback
        traceback.print_exc()

    print("----------------------------------------")
    print("Testing scrape_meshmanga_latest...")
    try:
        updates = await scrape_meshmanga_latest("https://meshmanga.com")
        print(f"SUCCESS. Latest updates count: {len(updates)}")
        for update in updates[:3]:
            print(f" - {update}")
    except Exception as e:
        print(f"FAILED. Latest scrape error: {e}")
        import traceback
        traceback.print_exc()

    print("----------------------------------------")
    print("Testing scrape_meshmanga_details...")
    try:
        details = await scrape_meshmanga_details("https://meshmanga.com/series/the-reincarnation-magician-of-the-inferior-eyes/")
        print(f"SUCCESS. Details response keys: {list(details.keys())}")
        print(f" - Description snippet: {details.get('description', '')[:100]}...")
        print(f" - Genres: {details.get('genres')}")
        print(f" - Chapters count: {len(details.get('chapters', []))}")
        for ch in details.get('chapters', [])[:3]:
            print(f"   * {ch}")
    except Exception as e:
        print(f"FAILED. Details scrape error: {e}")
        import traceback
        traceback.print_exc()

    print("----------------------------------------")
    print("Testing scrape_meshmanga_pages...")
    try:
        pages = await scrape_meshmanga_pages("https://meshmanga.com/chapters/623161/")
        print(f"SUCCESS. Pages count: {len(pages)}")
        for p in pages[:3]:
            print(f" - {p}")
    except Exception as e:
        print(f"FAILED. Pages scrape error: {e}")
        import traceback
        traceback.print_exc()
    print("----------------------------------------")

if __name__ == "__main__":
    asyncio.run(test())
