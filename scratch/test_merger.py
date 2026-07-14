import sys
import os
import asyncio
import re

# Add the parent directory to system path so we can import from core/main
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import extract_chapter_number, infer_chapter_numbers

def normalize_text(text) -> str:
    if not isinstance(text, str):
        return ""
    text = text.lower().strip()
    text = re.sub(r'\b(?:الفصل|فصل|شابتر|chapter|ch|ep|episode)\b', '', text)
    text = re.sub(r'[أإآ]', 'ا', text)
    text = text.replace('ة', 'ه').replace('ى', 'ي')
    return "".join(re.findall(r'\w+', text))

# Mock scrapers returning both valid and corrupted chapter inputs
async def mock_scrape_meshmanga_details(url):
    return {
        "description": "MeshManga Description",
        "genres": ["Action", "Adventure"],
        "chapters": [
            None, # Corrupted: None instead of dict -> skipped
            "Raw string item", # Corrupted: String instead of dict -> skipped
            [1, 2, 3], # Corrupted: List instead of dict -> skipped
            {"title": "Chapter 90", "url": 12345}, # Valid parsing (non-string URL is cast to "12345") -> deduplicates with 90
            {"title": "Chapter 90", "url": None}, # Valid parsing (None URL is cast to "") -> deduplicates with 90
            {"title": "Chapter 90", "url": "https://meshmanga.com/chapters/90/"}, # Valid
            {"title": "89", "url": "https://meshmanga.com/chapters/89/"}, # Valid
            {"title": "الفصل 88.0", "url": "https://meshmanga.com/chapters/88/"}, # Valid
            {"title": "Chapter 86", "url": "https://meshmanga.com/chapters/86/"}, # Valid
            {"title": "Chapter 85", "url": "https://meshmanga.com/chapters/85/"}, # Valid
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
    print("Starting Merge-Then-Infer pipeline logic test with corrupted inputs...")

    primary_res = await mock_scrape_meshmanga_details("https://meshmanga.com/series/manga/")
    secondary_res = await mock_scrape_madara_details("https://olympustaff.com/series/manga/")
    
    description = primary_res.get("description") or ""
    genres = set(primary_res.get("genres") or [])
    
    primary_chapters = primary_res.get("chapters", [])
    if not isinstance(primary_chapters, list):
        primary_chapters = []
        
    merged_chapters = {}
    
    # Process Primary Source with Defensive Guards
    for ch in primary_chapters:
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
                
            ch_payload = {
                "title": ch_title,
                "url": ch_url,
                "extracted_number": ch_num
            }
            key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
            merged_chapters[key] = ch_payload
        except Exception as loop_exc:
            print(f"Defensively skipped primary chapter: {str(loop_exc)}")
            continue
            
    # Process Secondary Source (Gap-Filling / Extension) with Defensive Guards
    if secondary_res:
        if not description and secondary_res.get("description"):
            description = secondary_res["description"]
        if secondary_res.get("genres"):
            genres.update(secondary_res["genres"])
            
        secondary_chapters = secondary_res.get("chapters", [])
        if not isinstance(secondary_chapters, list):
            secondary_chapters = []
            
        for ch in secondary_chapters:
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
                    
                ch_payload = {
                    "title": ch_title,
                    "url": ch_url,
                    "extracted_number": ch_num
                }
                key = f"num_{ch_num}" if ch_num != -1.0 else f"text_{normalize_text(ch_title)}"
                if key not in merged_chapters:
                    merged_chapters[key] = ch_payload
            except Exception as loop_exc:
                print(f"Defensively skipped secondary chapter: {str(loop_exc)}")
                continue

    # Sort descending by extracted number (stable sort)
    sorted_chapters = sorted(merged_chapters.values(), key=lambda x: x["extracted_number"], reverse=True)
    
    # Run infer_chapter_numbers exactly once on the unified sequence
    final_chapters = infer_chapter_numbers(sorted_chapters)
    
    # Assertions:
    # 1. Total count of chapters should be 10 (corrupted items in primary were skipped/cleaned, not crashed)
    assert len(final_chapters) == 10, f"Expected 10 chapters, but got {len(final_chapters)}"
    
    # 2. Sequential ascending order check
    expected_nums = [93.0, 92.0, 91.0, 90.0, 89.0, 88.0, 87.0, 86.0, 85.0, 84.0]
    actual_nums = [ch["chapter_number"] for ch in final_chapters]
    assert actual_nums == expected_nums, f"Expected sequence {expected_nums}, but got {actual_nums}"
    
    # 3. Check title deduplication & Primary source precedence:
    ch_90 = next(ch for ch in final_chapters if ch["chapter_number"] == 90.0)
    assert "meshmanga.com" in ch_90["url"]
    assert ch_90["title"] == "Chapter 90"
    
    print("All Merge-Then-Infer pipeline tests with corrupted inputs passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_test())
