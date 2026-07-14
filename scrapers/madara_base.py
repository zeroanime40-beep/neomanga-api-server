import httpx
import re
import logging
import asyncio
from typing import Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin

logger = logging.getLogger("uvicorn")

def print(*args, **kwargs):
    msg = " ".join(str(arg) for arg in args)
    logger.info(msg)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

NSFW_BLOCKLIST = ["+18", "18+", "محتوى غير لائق", "المحتوى غير لائق"]

# Precompiled regex patterns for performance optimization (P6)
REGEX_THUMBNAIL_SIZE = re.compile(r'-\d+x\d+')
REGEX_MANGA_ID_SCRIPT = re.compile(r'mangaId\s*=\s*["\']?(\d+)["\']?', re.IGNORECASE)
REGEX_MANGA_ID_HTML = [
    re.compile(r'manga_id\s*=\s*["\']?(\d+)["\']?', re.IGNORECASE),
    re.compile(r'mangaID\s*=\s*["\']?(\d+)["\']?', re.IGNORECASE),
    re.compile(r'post\s*=\s*["\']?(\d+)["\']?', re.IGNORECASE)
]

def construct_page_url(base_url: str, page: int) -> str:
    """
    Constructs pagination URLs for Madara / Olympus themes.
    Maps pagination URL correctly (e.g., {site_url}/manga/page/{page}/?m_order=views).
    """
    from urllib.parse import urlparse
    
    # Reroute olympustaff.com catalog requests targeting the homepage to the /series catalog path
    if "olympustaff.com" in base_url.lower():
        parsed_url = urlparse(base_url)
        clean_path = parsed_url.path.strip("/")
        if clean_path in ["", "series"]:
            base_url = "https://olympustaff.com/series"
            
    # Check if a custom placeholder `{page}` exists in base_url
    if "{page}" in base_url:
        return base_url.replace("{page}", str(page))
        
    if "?" in base_url:
        parts = base_url.split("?", 1)
        path = parts[0].rstrip("/")
        query = parts[1]
    else:
        path = base_url.rstrip("/")
        query = ""

    parsed = urlparse(path)
    path_str = parsed.path.rstrip("/")
    
    query_parts = []
    if query:
        query_parts = [q for q in query.split("&") if not (q.startswith("page=") or q.startswith("paged="))]
    
    if page == 1:
        has_m_order = any(q.startswith("m_order=") for q in query_parts)
        if not has_m_order:
            query_parts.append("m_order=views")
        query_str = "&".join(query_parts)
        return f"{path}/?{query_str}"
        
    is_homepage = (path_str == "" or path_str == "/")
    is_series = "/series" in path_str.lower()
    
    if is_homepage or is_series:
        query_parts.append(f"page={page}")
        has_m_order = any(q.startswith("m_order=") for q in query_parts)
        if not has_m_order:
            query_parts.append("m_order=views")
        query_str = "&".join(query_parts)
        return f"{path}/?{query_str}"
    else:
        if not (path_str.endswith("/manga") or "/manga/" in path_str or "/manga-list" in path_str):
            path = f"{path}/manga"
            
        path = f"{path}/page/{page}/"
        has_m_order = any(q.startswith("m_order=") for q in query_parts)
        if not has_m_order:
            query_parts.append("m_order=views")
        query_str = "&".join(query_parts)
        return f"{path}?{query_str}"

def parse_madara_html(html: str, base_url: str) -> list:
    """
    Parses a single HTML page using Madara / Olympus selectors, applies NSFW filtering,
    and returns a list of dictionaries with title, url, thumbnail, and latest_chapter.
    Synchronous helper designed to run in background thread via lxml (P1 & P2).
    """
    manga_list = []
    seen_urls = set()
    
    soup = BeautifulSoup(html, "lxml")
    
    # Locate latest updates / catalog items.
    if "olympustaff.com" in base_url.lower() and "/series" in base_url.lower():
        containers = soup.select("div.listupd div.bs div.bsx")
    else:
        containers = soup.select(
            "div.listupd div.bs div.bsx, .page-item-detail, .manga-item, .post-item, .manga-entry, .uta, .entry-box, .swiper-slide, .bsx"
        )
    
    # Fallback to post titles directly if layout is different
    if not containers:
        containers = soup.select(".post-title, .manga-name")
 
    for container in containers:
        # Extract title and absolute URL
        title_el = None
        title_anchor = None
        
        # Candidate selectors for title elements
        title_selectors = [
            ".tt", ".post-title a", ".manga-name a", ".h5 a", 
            ".entry-title a", ".info a", "h3 a", 
            "h4 a", "h5 a", "h3", "h4", "h5", "a"
        ]
        
        for selector in title_selectors:
            elements = container.select(selector)
            for el in elements:
                anchor = el if el.name == "a" else (el.find_parent("a") or el.find("a"))
                if anchor and anchor.get("href"):
                    title_text = el.get_text().strip()
                    if title_text:
                        title_el = el
                        title_anchor = anchor
                        break
            if title_el:
                break
        
        if not title_el:
            continue
            
        title = title_el.get_text().strip()
        href = title_anchor.get("href", "")
        if not title or not href:
            continue
            
        url = urljoin(base_url, href)
        
        # Avoid duplicate parsing of the same manga card on a single page
        if url in seen_urls:
            continue
            
        # Strict NSFW Filter
        title_lower = title.lower()
        is_nsfw = False
        for word in NSFW_BLOCKLIST:
            if word.lower() in title_lower:
                is_nsfw = True
                break
        
        if is_nsfw:
            print(f"[Scraper] Filtering out NSFW/Blocked manga title: {title}")
            continue
            
        # Extract thumbnail (look for lazy-load options first, then fallback to src)
        img_el = container.select_one("img")
        thumbnail = ""
        if img_el:
            for attr in ["data-src", "data-lazy-src", "data-cfsrc", "src"]:
                img_val = img_el.get(attr)
                if img_val:
                    thumbnail = urljoin(base_url, img_val.strip())
                    break
        
        if thumbnail:
            # Clean WordPress low-res/blurry size suffix (e.g. -300x375)
            thumbnail = REGEX_THUMBNAIL_SIZE.sub('', thumbnail)
            # Clean Olympus Staff thumbnail prefix to get high-res original cover
            thumbnail = thumbnail.replace("/thumbnail_", "/")
        
        # Extract latest chapter text and URL if possible
        chapter_el = container.select_one(".chapter a, .chapter-link, .list-chapter a, .chapters a, li a, .chapter-item a")
        if not chapter_el:
            # Search for any child anchor that contains "chapter" in text or href
            for a_tag in container.select("a"):
                href_val = a_tag.get("href", "")
                text_val = a_tag.get_text()
                if "chapter" in href_val.lower() or "chapter" in text_val.lower():
                    chapter_el = a_tag
                    break
                    
        if chapter_el:
            raw_chapter = chapter_el.get_text()
            # Collapse spaces and strip whitespace
            latest_chapter = " ".join(raw_chapter.split())
        else:
            latest_chapter = "Unknown"
        
        manga_list.append({
            "title": title,
            "url": url,
            "thumbnail": thumbnail,
            "latest_chapter": latest_chapter
        })
        seen_urls.add(url)
        
    return manga_list

def parse_madara_details_html(html: str, manga_url: str) -> tuple:
    """
    Synchronous helper to parse description, genres, and manga ID from HTML.
    Designed to run in background thread via lxml (P1 & P2).
    """
    soup = BeautifulSoup(html, "lxml")
    
    # 1. Extract Description/Synopsis
    description = ""
    for sel in [".summary__content", ".manga-summary", ".description-summary", ".post-content_item", ".review-content"]:
        desc_el = soup.select_one(sel)
        if desc_el:
            text = " ".join(desc_el.get_text().split()).strip()
            if text:
                description = text
                break
                
    # 2. Extract Genres
    genres = []
    genre_els = soup.select(".genres-content a, .manga-genres a, .summary-content.genres a, .review-author-info a")
    for el in genre_els:
        genre_name = el.get_text().strip()
        if genre_name and genre_name not in genres:
            genres.append(genre_name)
            
    # 3. Extract Manga ID
    manga_id = None
    manga_id_el = soup.find("input", attrs={"name": "manga_id"})
    if manga_id_el and manga_id_el.get("value"):
        manga_id = manga_id_el.get("value").strip()
        
    if not manga_id:
        action_btn = soup.select_one(".wp-manga-action-button[data-id]")
        if action_btn and action_btn.get("data-id"):
            manga_id = action_btn.get("data-id").strip()
            
    if not manga_id:
        for selector in ["[data-id]", "[data-post]", "input[id*='manga']"]:
            el = soup.select_one(selector)
            if el:
                for attr in ["data-id", "data-post", "value"]:
                    val = el.get(attr)
                    if val and val.strip().isdigit():
                        manga_id = val.strip()
                        break
                if manga_id:
                    break
                    
    if not manga_id:
        for script in soup.find_all("script"):
            script_text = script.string or ""
            match = REGEX_MANGA_ID_SCRIPT.search(script_text)
            if match:
                manga_id = match.group(1)
                break
                
    if not manga_id:
        for pattern in REGEX_MANGA_ID_HTML:
            match = pattern.search(html)
            if match:
                manga_id = match.group(1)
                break
                
    return description, genres, manga_id

def parse_madara_chapters_html(html: str, manga_url: str, seen_chapter_urls: set) -> list:
    """
    Synchronous helper to parse chapter anchor elements.
    Designed to run in background thread via lxml (P1 & P2).
    """
    soup_obj = BeautifulSoup(html, "lxml")
    manga_base_path = manga_url.rstrip("/").split("?")[0]
    
    page_chapters = []
    container_selectors = [
        "#chaptersContainer",
        ".enhanced-chapters-grid",
        ".enhanced-chapters-section",
        ".listing-chapters_wrap",
        ".manga-chapters-list",
        ".wp-manga-chapter-container",
        ".chapters-list",
        ".page-content-listing",
        ".manga-chapters",
        ".row-content-chapter",
        "#chapters-list"
    ]
    chapter_container = None
    for sel in container_selectors:
        element = soup_obj.select_one(sel)
        if element:
            chapter_container = element
            break
    target_soup = chapter_container if chapter_container else soup_obj
    
    anchors = target_soup.select(".wp-manga-chapter a, .chapter-link, .list-chapter a, .chapters a")
    if not anchors:
        anchors = [a for a in target_soup.select("a") if a.get("href") and "/chapter/" in a.get("href").lower()]
        
    for a in anchors:
        href = a.get("href", "").strip()
        if not href or href == "#":
            continue
        chapter_url = urljoin(manga_url, href)
        if chapter_url in seen_chapter_urls:
            continue
            
        # Ensure the chapter URL belongs to the target manga series path hierarchy
        if not (chapter_url == manga_base_path or 
                chapter_url.startswith(manga_base_path + "/") or 
                chapter_url.startswith(manga_base_path + "?")):
            continue
            
        title_text = " ".join(a.get_text().split())
        if not title_text:
            title_text = "Chapter"
            
        page_chapters.append({
            "title": title_text,
            "url": chapter_url
        })
        seen_chapter_urls.add(chapter_url)
    return page_chapters

def parse_madara_pages_html(html: str, chapter_url: str) -> list:
    """
    Synchronous helper to parse image page URLs from chapter HTML.
    Designed to run in background thread via lxml (P1 & P2).
    """
    soup = BeautifulSoup(html, "lxml")
    
    # Target image containers commonly used in Madara and custom themes
    img_selectors = [
        ".reading-content img",
        ".page-break img",
        "div.cha-img img",
        ".wp-manga-chapter-img",
        ".read-container img",
        ".chapter-content img",
        ".entry-content img",
        ".post-content img",
    ]
    
    seen_elements = set()
    ordered_imgs = []
    
    for selector in img_selectors:
        for img in soup.select(selector):
            if img not in seen_elements:
                seen_elements.add(img)
                ordered_imgs.append(img)
                
    # Fallback: look for images inside any container that looks like a page-break/reading-content
    if not ordered_imgs:
        for img in soup.find_all("img"):
            parent = img.parent
            parent_classes = []
            while parent and parent.name != "body":
                if parent.get("class"):
                    parent_classes.extend(parent.get("class"))
                parent = parent.parent
            classes_str = " ".join(parent_classes).lower()
            if any(k in classes_str for k in ["reading-content", "page-break", "cha-img", "wp-manga-chapter-img", "chapter-img", "manga-img"]):
                if img not in seen_elements:
                    seen_elements.add(img)
                    ordered_imgs.append(img)
                    
    # Last resort fallback: all images in post/article body
    if not ordered_imgs:
        for img in soup.select("article img, .entry-content img, .post-content img, .page img"):
            if img not in seen_elements:
                seen_elements.add(img)
                ordered_imgs.append(img)
                
    image_urls = []
    for img in ordered_imgs:
        img_url = ""
        for attr in ["data-src", "data-lazy-src", "data-cfsrc", "src"]:
            val = img.get(attr)
            if val:
                img_url = val.strip()
                break
                
        if not img_url:
            continue
            
        # Absolute-join image URL
        absolute_url = urljoin(chapter_url, img_url)
        url_lower = absolute_url.lower()
        
        # Filter tracking, advertising, empty strings, and placeholders
        is_tracking_or_ad = False
        ad_keywords = ["pixel", "analytics", "tracking", "statcounter", "histats", "google-analytics", "doubleclick", "adsystem", "favicon"]
        for kw in ad_keywords:
            if kw in url_lower:
                is_tracking_or_ad = True
                break
                
        if url_lower.startswith("data:image"):
            if "r0lgodlhaqabaia" in url_lower or "r0lgodlhaqabaid" in url_lower or "empty" in url_lower:
                is_tracking_or_ad = True
                
        if any(logo_p in url_lower for logo_p in ["/logo.png", "/logo.jpg", "/logo-", "/banner", "/avatar"]):
            is_tracking_or_ad = True
            
        if is_tracking_or_ad:
            continue
            
        if absolute_url not in image_urls:
            image_urls.append(absolute_url)
            
    return image_urls

async def scrape_madara_latest(base_url: str, client: Optional[httpx.AsyncClient] = None) -> list:
    """
    Asynchronously scrape the latest updates page of a given manga website (Madara or TS/Olympus layout).
    """
    print(f"[Scraper] Preparing to fetch latest updates page from {base_url}...")
    
    if client is not None:
        response = await client.get(base_url, headers=HEADERS, timeout=5.0)
        response.raise_for_status()
        html = response.text
    else:
        async with httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True) as local_client:
            response = await local_client.get(base_url)
            response.raise_for_status()
            html = response.text

    manga_list = await asyncio.to_thread(parse_madara_html, html, base_url)
    print(f"[Scraper] Success: Fetched and parsed {len(manga_list)} items from {base_url}")
    return manga_list

async def scrape_madara_catalog(base_url: str, page: int, client: Optional[httpx.AsyncClient] = None) -> list:
    """
    Scrapes a specific page of a Madara / Olympus site catalog.
    """
    all_manga = []
    seen_urls = set()
    
    page_url = construct_page_url(base_url, page)
    print(f"[Scraper] Scraping catalog page {page}: {page_url}")
    
    if client is not None:
        response = await client.get(page_url, headers=HEADERS, timeout=5.0)
        response.raise_for_status()
        html = response.text
    else:
        async with httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True) as local_client:
            response = await local_client.get(page_url)
            response.raise_for_status()
            html = response.text
            
    page_items = await asyncio.to_thread(parse_madara_html, html, base_url)
    
    # De-duplicate items based on url
    for item in page_items:
        if item["url"] not in seen_urls:
            all_manga.append(item)
            seen_urls.add(item["url"])
                
    print(f"[Scraper] Completed catalog page {page} scrape. Total items: {len(all_manga)}")
    return all_manga

async def scrape_madara_details(manga_url: str, client: Optional[httpx.AsyncClient] = None) -> dict:
    """
    Scrapes the details page of a specific manga.
    Returns a dictionary containing description, genres, and a list of chapters.
    """
    print(f"[Scraper] Preparing to fetch manga details from {manga_url}...")
    
    from urllib.parse import urlparse, urlunparse
    parsed_manga_url = urlparse(manga_url)
    clean_path = parsed_manga_url.path.rstrip("/")
    ajax_path = f"{clean_path}/ajax/chapters/"
    ajax_url = urlunparse((parsed_manga_url.scheme, parsed_manga_url.netloc, ajax_path, "", "", ""))
    
    active_client = client
    client_is_shared = active_client is not None
    
    if not client_is_shared:
        active_client = httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True)
        
    try:
        # First fetch the main page to get description and genres
        if client_is_shared:
            response = await active_client.get(manga_url, headers=HEADERS, timeout=5.0)
        else:
            response = await active_client.get(manga_url)
            
        response.raise_for_status()
        html = response.text
        
        # Parse synopsis metadata off the event loop
        description, genres, manga_id = await asyncio.to_thread(parse_madara_details_html, html, manga_url)

        ajax_chapters_html = None
        
        # Method A: admin-ajax.php POST
        if manga_id:
            admin_ajax_url = f"{parsed_manga_url.scheme}://{parsed_manga_url.netloc}/wp-admin/admin-ajax.php"
            try:
                print(f"[Scraper] Fetching complete chapters list via admin-ajax POST from {admin_ajax_url} for manga ID {manga_id}...")
                if client_is_shared:
                    ajax_res = await active_client.post(
                        admin_ajax_url,
                        headers={
                            "User-Agent": HEADERS["User-Agent"],
                            "Content-Type": "application/x-www-form-urlencoded"
                        },
                        data={
                            "action": "manga_get_chapters",
                            "manga": manga_id
                        },
                        timeout=5.0
                    )
                else:
                    ajax_res = await active_client.post(
                        admin_ajax_url,
                        headers={
                            "User-Agent": HEADERS["User-Agent"],
                            "Content-Type": "application/x-www-form-urlencoded"
                        },
                        data={
                            "action": "manga_get_chapters",
                            "manga": manga_id
                        }
                    )
                if ajax_res.status_code == 200 and ajax_res.text.strip():
                    temp_soup = BeautifulSoup(ajax_res.text, "lxml")
                    temp_anchors = temp_soup.select(".wp-manga-chapter a, .chapter-link, .list-chapter a, .chapters a")
                    if not temp_anchors:
                        temp_anchors = [a for a in temp_soup.select("a") if a.get("href") and "/chapter/" in a.get("href").lower()]
                    if temp_anchors:
                        ajax_chapters_html = ajax_res.text
                        print(f"[Scraper] Successfully loaded {len(temp_anchors)} chapters from admin-ajax.")
                    else:
                        print("[Scraper] admin-ajax response returned no chapter elements. Trying other methods...")
                else:
                    print(f"[Scraper] admin-ajax POST returned status {ajax_res.status_code}")
            except Exception as admin_exc:
                print(f"[Scraper] admin-ajax fetch failed: {str(admin_exc)}")
                
        # Method B: Path AJAX Fallback
        if not ajax_chapters_html:
            try:
                print(f"[Scraper] Fetching chapters via path AJAX POST from {ajax_url}...")
                if client_is_shared:
                    ajax_res = await active_client.post(ajax_url, headers=HEADERS, timeout=5.0)
                else:
                    ajax_res = await active_client.post(ajax_url)
                if ajax_res.status_code == 200 and ajax_res.text.strip():
                    ajax_chapters_html = ajax_res.text
                else:
                    print(f"[Scraper] Path AJAX POST returned status {ajax_res.status_code}. Trying GET...")
                    if client_is_shared:
                        ajax_res = await active_client.get(ajax_url, headers=HEADERS, timeout=5.0)
                    else:
                        ajax_res = await active_client.get(ajax_url)
                    if ajax_res.status_code == 200 and ajax_res.text.strip():
                        ajax_chapters_html = ajax_res.text
            except Exception as path_exc:
                print(f"[Scraper] Path AJAX fetch failed: {str(path_exc)}")

        chapters = []
        seen_chapter_urls = set()
        
        # Compile all chapters
        if ajax_chapters_html:
            parsed_ch = await asyncio.to_thread(parse_madara_chapters_html, ajax_chapters_html, manga_url, seen_chapter_urls)
            chapters.extend(parsed_ch)
            
        if not chapters:
            print("[Scraper] No chapters found via AJAX. Parsing main page HTML and running sequential traversal...")
            
            page_num = 1
            while True:
                if page_num == 1:
                    parsed_ch = await asyncio.to_thread(parse_madara_chapters_html, html, manga_url, seen_chapter_urls)
                else:
                    page_url = f"{manga_url}?page={page_num}"
                    try:
                        print(f"[Scraper] Fetching details page {page_num}: {page_url}")
                        if client_is_shared:
                            res_page = await active_client.get(page_url, headers=HEADERS, timeout=5.0)
                        else:
                            res_page = await active_client.get(page_url)
                            
                        if res_page.status_code != 200:
                            print(f"[Scraper] Details page {page_num} returned status {res_page.status_code}. Terminating traversal.")
                            break
                        parsed_ch = await asyncio.to_thread(parse_madara_chapters_html, res_page.text, manga_url, seen_chapter_urls)
                    except Exception as page_exc:
                        print(f"[Scraper] Failed to fetch details page {page_num}: {str(page_exc)}. Terminating traversal.")
                        break
                
                if not parsed_ch:
                    print(f"[Scraper] No chapters found on details page {page_num}. Terminating traversal.")
                    break
                
                print(f"[Scraper] Found {len(parsed_ch)} chapters on details page {page_num}.")
                chapters.extend(parsed_ch)
                page_num += 1

        chapters.reverse()
        print(f"[Scraper] Success: Fetched details for {manga_url}. Total chapters found: {len(chapters)}")
        return {
            "description": description,
            "genres": genres,
            "total_chapters": len(chapters),
            "chapters": chapters
        }
    finally:
        if not client_is_shared:
            await active_client.aclose()

async def scrape_madara_pages(chapter_url: str, client: Optional[httpx.AsyncClient] = None) -> list:
    """
    Asynchronously scrape reading image URLs from a specific chapter page.
    """
    print(f"[Scraper] Preparing to fetch chapter pages from {chapter_url}...")
    
    if client is not None:
        response = await client.get(chapter_url, headers=HEADERS, timeout=5.0)
        response.raise_for_status()
        html = response.text
    else:
        async with httpx.AsyncClient(headers=HEADERS, timeout=5.0, follow_redirects=True) as local_client:
            response = await local_client.get(chapter_url)
            response.raise_for_status()
            html = response.text
        
    image_urls = await asyncio.to_thread(parse_madara_pages_html, html, chapter_url)
    print(f"[Scraper] Success: Extracted {len(image_urls)} page image URLs from {chapter_url}")
    return image_urls
