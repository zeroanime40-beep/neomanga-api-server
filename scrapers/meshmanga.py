import httpx
import re
import logging
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("uvicorn")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

async def scrape_meshmanga_latest(site_url: str, client: Optional[httpx.AsyncClient] = None) -> list:
    """
    Scrapes latest updates from MeshManga by fetching the series sorted by updated_at.
    """
    api_url = "https://appswat.com/v2/api/v2/series/?ordering=-updated_at&page=1"
    
    if client is not None:
        response = await client.get(api_url, headers=HEADERS, timeout=5.0)
        response.raise_for_status()
        data = response.json()
    else:
        async with httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True) as local_client:
            response = await local_client.get(api_url)
            response.raise_for_status()
            data = response.json()
        
    results = data.get("results", [])
    updates = []
    for item in results:
        slug = item.get("slug")
        if not slug:
            continue
        poster = item.get("poster") or {}
        thumbnail = poster.get("thumbnail") or poster.get("medium") or ""
        
        updates.append({
            "title": item.get("title", ""),
            "url": f"https://meshmanga.com/series/{slug}/",
            "thumbnail": thumbnail,
            "latest_chapter": None
        })
    return updates

async def scrape_meshmanga_catalog(site_url: str, page: int, client: Optional[httpx.AsyncClient] = None) -> list:
    """
    Scrapes the manga catalog from MeshManga API.
    """
    api_url = f"https://appswat.com/v2/api/v2/series/?page={page}"
    
    if client is not None:
        response = await client.get(api_url, headers=HEADERS, timeout=5.0)
        response.raise_for_status()
        data = response.json()
    else:
        async with httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True) as local_client:
            response = await local_client.get(api_url)
            response.raise_for_status()
            data = response.json()
        
    results = data.get("results", [])
    items = []
    for item in results:
        slug = item.get("slug")
        if not slug:
            continue
        poster = item.get("poster") or {}
        thumbnail = poster.get("thumbnail") or poster.get("medium") or ""
        
        items.append({
            "title": item.get("title", ""),
            "url": f"https://meshmanga.com/series/{slug}/",
            "thumbnail": thumbnail,
            "latest_chapter": ""
        })
    return items

async def get_series_id_by_slug(client: httpx.AsyncClient, slug: str) -> int:
    """
    Resolves the series ID on the MeshManga backend API for a given slug.
    """
    search_term = slug.replace("-", " ")
    search_url = f"https://appswat.com/v2/api/v2/series/?search={search_term}"
    res = await client.get(search_url)
    res.raise_for_status()
    data = res.json()
    
    results = data.get("results", [])
    
    # 1. Exact match on slug
    for item in results:
        if item.get("slug") == slug:
            return item["id"]
            
    # 2. Case-insensitive slug contains match
    for item in results:
        if slug.lower() in item.get("slug", "").lower():
            return item["id"]
            
    # 3. Fallback to first search result
    if results:
        return results[0]["id"]
        
    raise Exception(f"Manga with slug '{slug}' not found on MeshManga API search")

async def scrape_meshmanga_details(manga_url: str, client: Optional[httpx.AsyncClient] = None) -> dict:
    """
    Fetches details and all chapters for a given manga URL from MeshManga API.
    """
    parsed = urlparse(manga_url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        raise Exception(f"Invalid manga URL: {manga_url}")
    slug = path_parts[-1]
    
    active_client = client
    client_is_shared = active_client is not None
    
    if not client_is_shared:
        active_client = httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True)
        
    try:
        # Resolve ID by slug
        series_id = await get_series_id_by_slug(active_client, slug)
        logger.info(f"[MeshManga] Resolved slug '{slug}' to series ID {series_id}")
        
        # Get details
        details_url = f"https://appswat.com/v2/api/v2/series/{series_id}/"
        if client_is_shared:
            detail_res = await active_client.get(details_url, headers=HEADERS, timeout=5.0)
        else:
            detail_res = await active_client.get(details_url)
            
        detail_res.raise_for_status()
        detail_data = detail_res.json()
        
        story = detail_data.get("story", "")
        genres = [g.get("name") for g in detail_data.get("genres", []) if g.get("name")]
        
        # Fetch all chapters
        chapters = []
        page_url = f"https://appswat.com/v2/api/v2/series/{series_id}/chapters/"
        while page_url:
            if client_is_shared:
                ch_res = await active_client.get(page_url, headers=HEADERS, timeout=5.0)
            else:
                ch_res = await active_client.get(page_url)
                
            ch_res.raise_for_status()
            ch_data = ch_res.json()
            
            for ch in ch_data.get("results", []):
                ch_id = ch.get("id")
                ch_num = ch.get("chapter", "")
                if ch_id:
                    chapters.append({
                        "title": ch.get("title") or f"Chapter {ch_num}",
                        "url": f"https://meshmanga.com/chapters/{ch_id}/"
                    })
            page_url = ch_data.get("next")
            
        return {
            "description": story,
            "genres": genres,
            "total_chapters": len(chapters),
            "chapters": chapters
        }
    finally:
        if not client_is_shared:
            await active_client.aclose()

async def scrape_meshmanga_pages(chapter_url: str, client: Optional[httpx.AsyncClient] = None) -> list:
    """
    Fetches the list of page images for a chapter URL on MeshManga.
    """
    match = re.search(r'/chapters/(\d+)', chapter_url)
    if not match:
        raise Exception(f"Could not extract chapter ID from URL: {chapter_url}")
    chapter_id = match.group(1)
    
    api_url = f"https://appswat.com/v2/api/v2/chapters/{chapter_id}/"
    
    if client is not None:
        response = await client.get(api_url, headers=HEADERS, timeout=5.0)
        response.raise_for_status()
        data = response.json()
    else:
        async with httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True) as local_client:
            response = await local_client.get(api_url)
            response.raise_for_status()
            data = response.json()
        
    images = data.get("images", [])
    # Extract images sorted by their 'order' value
    sorted_images = sorted(images, key=lambda x: x.get("order", 0))
    page_urls = [img.get("image") for img in sorted_images if img.get("image")]
    return page_urls
