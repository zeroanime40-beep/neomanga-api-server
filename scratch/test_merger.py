import sys
import os
import asyncio
import re

# Add the parent directory to system path so we can import from core/main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import extract_chapter_number, infer_chapter_numbers

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'\b(?:الفصل|فصل|شابتر|chapter|ch|ep|episode)\b', '', text)
    text = re.sub(r'[أإآ]', 'ا', text)
    text = text.replace('ة', 'ه').replace('ى', 'ي')
    return "".join(re.findall(r'\w+', text))

# Let's write mock scrapers that return titles in various formats to trigger sorting/stacking bugs
async def mock_scrape_meshmanga_details(url):
    return {
        "description": "MeshManga Description",
        "genres": ["Action", "Adventure"],
        "chapters": [
            {"title": "Chapter 90", "url": "https://meshmanga.com/chapters/90/"},
            {"title": "89", "url": "https://meshmanga.com/chapters/89/"},
            {"title": "الفصل 88.0", "url": "https://meshmanga.com/chapters/88/"},
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
            {"title": "الفصل 93", "url": "https://olympustaff.com/series/manga/chapter-93/"},
            {"title": "92", "url": "https://olympustaff.com/series/manga/chapter-92/"},
            {"title": "Chapter 91", "url": "https://olympustaff.com/series/manga/chapter-91/"},
            {"title": "الفصل 90.0", "url": "https://olympustaff.com/series/manga/chapter-90/"},
            {"title": "Chapter 89", "url": "https://olympustaff.com/series/manga/chapter-89/"},
            {"title": "الفصل 88", "url": "https://olympustaff.com/series/manga/chapter-88/"},
            {"title": "Chapter 87", "url": "https://olympustaff.com/series/manga/chapter-87/"}, # Gap filler
            {"title": "Chapter 86", "url": "https://olympustaff.com/series/manga/chapter-86/"},
            {"title": "Chapter 85", "url": "https://olympustaff.com/series/manga/chapter-85/"},
            {"title": "Chapter 84", "url": "https://olympustaff.com/series/manga/chapter-84/"}, # Extra old
        ]
    }

async def run_test():
    print("Starting Merge-Then-Infer pipeline logic test...")

    # Case 1: MeshManga is Primary (fastest), Madara is Secondary.
    # Primary has chapters 90 (Chapter 90), 89 (89), 88 (الفصل 88.0), 86, 85
    # Secondary has chapters 93, 92, 91, 90 (الفصل 90.0), 89, 88 (الفصل 88), 87, 86, 85, 84
    
    primary_res = await mock_scrape_meshmanga_details("https://meshmanga.com/series/manga/")
    secondary_res = await mock_scrape_madara_details("https://olympustaff.com/series/manga/")
    
    description = primary_res.get("description") or ""
    genres = set(primary_res.get("genres") or [])
    
    primary_chapters = primary_res.get("chapters", [])
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
        
    # Verify primary population
    assert "num_90.0" in merged_chapters
    assert "num_89.0" in merged_chapters
    assert "num_88.0" in merged_chapters
    
    # Process Secondary Source (Gap-Filling / Extension)
    if secondary_res:
        if not description and secondary_res.get("description"):
            description = secondary_res["description"]
        if secondary_res.get("genres"):
            genres.update(secondary_res["genres"])
            
        secondary_chapters = secondary_res.get("chapters", [])
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

    # Sort descending by extracted number (stable sort)
    sorted_chapters = sorted(merged_chapters.values(), key=lambda x: x["extracted_number"], reverse=True)
    
    # Run infer_chapter_numbers exactly once on the unified sequence
    final_chapters = infer_chapter_numbers(sorted_chapters)
    
    # Assertions:
    # 1. Total count of chapters should be 10 (no stacks/blocks, correct deduplication)
    assert len(final_chapters) == 10, f"Expected 10 chapters, but got {len(final_chapters)}"
    
    # 2. Sequential ascending order check
    expected_nums = [93.0, 92.0, 91.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0]
    actual_nums = [ch["chapter_number"] for ch in final_chapters]
    assert actual_nums == expected_nums, f"Expected sequence {expected_nums}, but got {actual_nums}"
    
    # 3. Check title deduplication & Primary source precedence:
    # - Chapter 90 ("Chapter 90" vs "الفصل 90.0"): should use MeshManga URL (Primary)
    ch_90 = next(ch for ch in final_chapters if ch["chapter_number"] == 90.0)
    assert "meshmanga.com" in ch_90["url"]
    assert ch_90["title"] == "Chapter 90"
    
    # - Chapter 89 ("89" vs "Chapter 89"): should use MeshManga URL
    ch_89 = next(ch for ch in final_chapters if ch["chapter_number"] == 89.0)
    assert "meshmanga.com" in ch_89["url"]
    assert ch_89["title"] == "89"
    
    # - Chapter 88 ("الفصل 88.0" vs "الفصل 88"): should use MeshManga URL
    ch_88 = next(ch for ch in final_chapters if ch["chapter_number"] == 88.0)
    assert "meshmanga.com" in ch_88["url"]
    assert ch_88["title"] == "الفصل 88.0"
    
    # 4. Check secondary extensions & gap filling URLs:
    # - Chapter 93 (extension): should use Olympus URL
    ch_93 = next(ch for ch in final_chapters if ch["chapter_number"] == 93.0)
    assert "olympustaff.com" in ch_93["url"]
    
    # - Chapter 87 (gap): should use Olympus URL
    ch_87 = next(ch for ch in final_chapters if ch["chapter_number"] == 87.0)
    assert "olympustaff.com" in ch_87["url"]
    
    print("All Merge-Then-Infer pipeline tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_test())
