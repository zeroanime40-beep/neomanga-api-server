# 📌 Neo Manga Centralized Backend - Project Status & Roadmap

## 🗺️ Master Roadmap Summary
- [x] Phase 1: Base Architecture & Scraping Engine (100% Complete)
- [x] Phase 2: Database Layer, Deduplication & Catalog (100% Complete)
- [x] Phase 3: Master Extension APK Integration (100% Complete)
- [ ] Phase 4: Cloud Production Deployment (Upcoming)

---

## 🛠️ Detailed Status Breakdown

### 🟢 1. WHAT IS DONE (Completed Tasks)
* **Project Initialization:** Set up FastAPI, Uvicorn, Async HTTPX client, and BeautifulSoup4 architecture.
* **Flexible Scraper Engine:** Created `scrapers/madara_base.py` with multi-selector support capable of handling regular Madara themes and modern custom variants (like Olympus Staff tags: `.uta`, `.entry-box`, `.swiper-slide`).
* **Strict Security Filter:** Fully implemented a case-insensitive, keyword-based NSFW filter targeting `["+18", "18+", "محتوى غير لائق", "المحتوى غير لائق"]` to clean updates before delivery.
* **Live Testing Verification:** Successfully executed unit tests (`test_parser.py`) and confirmed a live response from https://olympustaff.com fetching 53 clean manga updates with text formatting and whitespaces thoroughly optimized.
* **Local Server Deployment:** Launched the ASGI live instance on http://127.0.0.1:8000.
* **MongoDB Layer Integration:** Installed `motor` async MongoDB driver, set up client/database pool inside `core/database.py`, and added a lifespan startup connection test in `main.py`.
* **Ingestion Logic & Catalog Endpoint:** Added `upsert_manga_entry()` in `core/database.py`, developed `scrape_madara_catalog()` in `scrapers/madara_base.py` for multi-page pagination scraping, and created the `/api/v1/manga/catalog` endpoint in `main.py` to scrape and auto-ingest items.
* **Database Exception Shield & Environment Config:** Wrapped database writes in `try-except` blocks to prevent offline db server freezes, and updated database initialization to read connection parameters from `MONGO_URI` environment variables.
* **Database Status Flag & Timeout Optimizations:** Added a 1-second server selection timeout limit and implemented an `IS_DB_ONLINE` state check in endpoints to completely skip database ingestion loops when offline, resolving sequential hanging delays.
* **Deduplication & Env Config:** Configured project-wide environment loading via `.env` and `python-dotenv`, and implemented multi-source catalog deduplication using clean slug matching inside `core/database.py`.
* **Manga Details & Chapters Scraper:** Implemented `scrape_madara_details()` in `scrapers/madara_base.py` with multi-selector scoping for synopsis/genres and strict URL path matching. Added `/api/v1/manga/details` endpoint to serve clean, ordered chapter lists and metadata.
* **Chapter Pages Scraper & Endpoint:** Implemented `scrape_madara_pages()` in `scrapers/madara_base.py` with multi-selector support and dynamic lazy-load and tracking/advertisement pixel filters. Added `/api/v1/chapters/pages` endpoint in `main.py` serving clean reading page arrays.
* **Automated Background Cron-job Scheduler:** Implemented periodic catalog auto-refresh using `apscheduler` to fetch updates every 60 minutes for configured sites (e.g. Olympus Staff) and ingest them into MongoDB when online.
* **App Connectivity (Phase 3 Integration):** Created a built-in network source extension `NeoMangaMasterExtension` that connects directly to the FastAPI server. Configured automated background catalog hydration and navigation routing natively within the Dashboard.
    * **Files Created/Modified:**
        * `[NEW]` [NeoMangaMasterExtension.kt](file:///d:/neomangatest/mihon/app/src/main/java/eu/kanade/tachiyomi/source/online/NeoMangaMasterExtension.kt) - Connects to local FastAPI backend for catalog, updates, details, and chapter pages.
        * `[MODIFY]` [AndroidSourceManager.kt](file:///d:/neomangatest/mihon/app/src/main/java/eu/kanade/tachiyomi/source/AndroidSourceManager.kt) - Registers `NeoMangaMasterExtension` as the priority `Team X` source.
        * `[MODIFY]` [GetEnabledSources.kt](file:///d:/neomangatest/mihon/app/src/main/java/eu/kanade/domain/source/interactor/GetEnabledSources.kt) - Bypasses language checks for the `Team X` source.
    * **Architecture Layout:**
        ```mermaid
        graph TD
            subgraph Mobile App
                D[DashboardScreen] --> |Requests| UC[GetUnifiedGlobalCatalogUseCase]
                UC --> |Resolves priority source| SM[SourceManager / AndroidSourceManager]
                SM --> |Loads| NME[NeoMangaMasterExtension]
            end
            subgraph Backend API Server
                NME --> |HTTP Calls| FA[FastAPI Endpoints]
                FA --> |latest/catalog/details/pages| S[Madara Scrapers]
                FA --> |Ingest/Fetch| DB[(MongoDB Cache)]
            end
        ```

### 🟡 2. WHAT IS HAPPENING NOW (Current Step)
* **Preparing for Phase 4 (Going Global):**
    * Preparing the FastAPI backend for VPS/Cloud infrastructure deployment.

### 🔵 3. WHAT IS LEFT TO DO (Upcoming Tasks)
* **Phase 4 (Going Global):** Deploy the backend to a VPS/Cloud infrastructure (Render/Railway) and configure production domains.

---
*Last Updated: 2026-07-13*

