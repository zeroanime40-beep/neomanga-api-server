import httpx

base_url = 'http://127.0.0.1:8000/api/v1'

def run_tests():
    print("--- Endpoint Test 1: Olympus Paused ---")
    res = httpx.get(f"{base_url}/manga/catalog?site_url=https://olympustaff.com")
    print(f"Status: {res.status_code}")
    print(f"Response: {res.json()}")

    print("\n--- Endpoint Test 2: MeshManga Catalog ---")
    res = httpx.get(f"{base_url}/manga/catalog?site_url=https://meshmanga.com")
    print(f"Status: {res.status_code}")
    catalog = res.json()
    print(f"Items count: {len(catalog.get('items', []))}")
    if catalog.get('items'):
        print(f"First item: {catalog['items'][0]}")

    print("\n--- Endpoint Test 3: MeshManga Details ---")
    res = httpx.get(f"{base_url}/manga/details?manga_url=https://meshmanga.com/series/the-reincarnation-magician-of-the-inferior-eyes/")
    print(f"Status: {res.status_code}")
    details = res.json()
    print(f"Genres: {details.get('genres')}")
    print(f"Chapters count: {len(details.get('chapters', []))}")
    if details.get('chapters'):
        print(f"First chapter: {details['chapters'][0]}")

    print("\n--- Endpoint Test 4: MeshManga Pages ---")
    res = httpx.get(f"{base_url}/chapters/pages?chapter_url=https://meshmanga.com/chapters/623161/")
    print(f"Status: {res.status_code}")
    pages = res.json()
    print(f"Pages count: {len(pages.get('pages', []))}")
    if pages.get('pages'):
        print(f"First page URL: {pages['pages'][0]}")

if __name__ == "__main__":
    run_tests()
