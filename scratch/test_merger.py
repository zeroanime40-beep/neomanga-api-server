import sys
import os
import asyncio

# Add the parent directory to system path so we can import from core/main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import infer_chapter_numbers

# Let's write some mock scrapers
async def mock_scrape_meshmanga_details(url):
    return {
        "description": "MeshManga Description",
        "genres": ["Action", "Adventure"],
        "chapters": [
            {"title": "Chapter 90", "url": "https://meshmanga.com/chapters/90/"},
            {"title": "Chapter 89", "url": "https://meshmanga.com/chapters/89/"},
            {"title": "Chapter 88", "url": "https://meshmanga.com/chapters/88/"},
            # Gap: Chapter 87 is missing!
            {"title": "Chapter 86", "url": "https://meshmanga.com/chapters/86/"},
            {"title": "Chapter 85", "url": "https://meshmanga.com/chapters/85/"},
        ]
    }

async def mock_scrape_madara_details(url):
    return {
        "description": "Madara Description",
        "genres": ["Action", "Fantasy"],
        "chapters": [
            {"title": "Chapter 93", "url": "https://olympustaff.com/series/manga/chapter-93/"},
            {"title": "Chapter 92", "url": "https://olympustaff.com/series/manga/chapter-92/"},
            {"title": "Chapter 91", "url": "https://olympustaff.com/series/manga/chapter-91/"},
            {"title": "Chapter 90", "url": "https://olympustaff.com/series/manga/chapter-90/"},
            {"title": "Chapter 89", "url": "https://olympustaff.com/series/manga/chapter-89/"},
            {"title": "Chapter 88", "url": "https://olympustaff.com/series/manga/chapter-88/"},
            {"title": "Chapter 87", "url": "https://olympustaff.com/series/manga/chapter-87/"}, # Gap filler
            {"title": "Chapter 86", "url": "https://olympustaff.com/series/manga/chapter-86/"},
            {"title": "Chapter 85", "url": "https://olympustaff.com/series/manga/chapter-85/"},
            {"title": "Chapter 84", "url": "https://olympustaff.com/series/manga/chapter-84/"}, # Extra old
        ]
    }

async def run_test():
    print("Starting simulated merging logic test...")

    # Case 1: MeshManga is Primary (Latency = 0.1s), Madara is Secondary (Latency = 0.5s)
    # Primary has chapters 90, 89, 88, 86, 85
    # Secondary has chapters 93, 92, 91, 90, 89, 88, 87, 86, 85, 84
    # Expected output:
    # - Primary chapters are populated.
    # - Extensions: 93, 92, 91 are appended from Secondary.
    # - Gaps: 87, 84 are patched.
    # - Total chapters: 93, 92, 91, 90, 89, 88, 87, 86, 85, 84 (10 chapters).
    # - Monotonicity is verified.
    
    primary_res = await mock_scrape_meshmanga_details("https://meshmanga.com/series/manga/")
    secondary_res = await mock_scrape_madara_details("https://olympustaff.com/series/manga/")
    
    description = primary_res.get("description") or ""
    genres = set(primary_res.get("genres") or [])
    
    primary_chapters = primary_res.get("chapters", [])
    primary_inferred = infer_chapter_numbers(primary_chapters)
    
    merged_chapters = {ch["chapter_number"]: {
        "title": ch.get("title", ""),
        "url": ch.get("url", ""),
        "chapter_number": ch["chapter_number"]
    } for ch in primary_inferred}
    
    # Verify primary population
    assert 90.0 in merged_chapters
    assert 87.0 not in merged_chapters
    assert 93.0 not in merged_chapters
    
    # Secondary merge
    if secondary_res:
        if not description and secondary_res.get("description"):
            description = secondary_res["description"]
        if secondary_res.get("genres"):
            genres.update(secondary_res["genres"])
            
        secondary_chapters = secondary_res.get("chapters", [])
        secondary_inferred = infer_chapter_numbers(secondary_chapters)
        
        max_primary_ch = max(merged_chapters.keys()) if merged_chapters else -float('inf')
        
        for ch in secondary_inferred:
            ch_num = ch["chapter_number"]
            ch_payload = {
                "title": ch.get("title", ""),
                "url": ch.get("url", ""),
                "chapter_number": ch_num
            }
            if ch_num > max_primary_ch:
                merged_chapters[ch_num] = ch_payload
            elif ch_num not in merged_chapters:
                merged_chapters[ch_num] = ch_payload
                
    # Sort descending
    sorted_chapters = sorted(merged_chapters.values(), key=lambda x: x["chapter_number"], reverse=True)
    final_chapters = infer_chapter_numbers(sorted_chapters)
    
    # Assertions
    assert len(final_chapters) == 10
    
    # The list should be in descending order: 93, 92, 91, 90, 89, 88, 87, 86, 85, 84
    expected_nums = [93.0, 92.0, 91.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0]
    actual_nums = [ch["chapter_number"] for ch in final_chapters]
    assert actual_nums == expected_nums, f"Expected {expected_nums}, but got {actual_nums}"
    
    # Check that MeshManga URL took priority for chapter 90
    ch_90 = next(ch for ch in final_chapters if ch["chapter_number"] == 90.0)
    assert "meshmanga.com" in ch_90["url"]
    
    # Check that Olympus URL is used for chapter 93 (extension) and 87 (gap)
    ch_93 = next(ch for ch in final_chapters if ch["chapter_number"] == 93.0)
    assert "olympustaff.com" in ch_93["url"]
    ch_87 = next(ch for ch in final_chapters if ch["chapter_number"] == 87.0)
    assert "olympustaff.com" in ch_87["url"]
    
    print("Test passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_test())
