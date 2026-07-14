import sys
import os

# Append project root to import path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import extract_chapter_number, infer_chapter_numbers

def test_inference_logic():
    print("Running Chapter Inference Tests...")

    # Case 1: Standard chapters and descriptive prologue at start
    chapters_case_1 = [
        {"title": "مواجهة الغوبلن", "url": "https://meshmanga.com/chapters/103/"},
        {"title": "الفصل 2", "url": "https://meshmanga.com/chapters/102/"},
        {"title": "الفصل 1", "url": "https://meshmanga.com/chapters/101/"},
        {"title": "مقدمة البطل", "url": "https://meshmanga.com/chapters/100/"},
    ]
    res_1 = infer_chapter_numbers(chapters_case_1)
    print("Case 1 output:")
    for i, c in enumerate(res_1):
        print(f"  Item {i}, Num: {c['chapter_number']}")
    assert abs(res_1[0]["chapter_number"] - 3.0) < 0.001
    assert abs(res_1[1]["chapter_number"] - 2.0) < 0.001
    assert abs(res_1[2]["chapter_number"] - 1.0) < 0.001
    assert abs(res_1[3]["chapter_number"] - 0.5) < 0.001  # Prologue gets 1.0 / 2 = 0.5

    # Case 2: All text-only chapters
    chapters_case_2 = [
        {"title": "البداية الجديدة", "url": "https://meshmanga.com/chapters/202/"},
        {"title": "العاصفة", "url": "https://meshmanga.com/chapters/201/"},
        {"title": "مواجهة الغوبلن", "url": "https://meshmanga.com/chapters/200/"},
    ]
    res_2 = infer_chapter_numbers(chapters_case_2)
    print("Case 2 output:")
    for i, c in enumerate(res_2):
        print(f"  Item {i}, Num: {c['chapter_number']}")
    assert abs(res_2[0]["chapter_number"] - 3.0) < 0.001
    assert abs(res_2[1]["chapter_number"] - 2.0) < 0.001
    assert abs(res_2[2]["chapter_number"] - 1.0) < 0.001

    # Case 3: Out of order parsed values (monotonic enforcement)
    chapters_case_3 = [
        {"title": "الفصل 3", "url": "https://meshmanga.com/chapters/303/"},
        {"title": "الفصل 2", "url": "https://meshmanga.com/chapters/302/"},
        # Typo: Title says chapter 4 instead of chapter 2.5
        {"title": "الفصل 4", "url": "https://meshmanga.com/chapters/301.5/"},
        {"title": "الفصل 1", "url": "https://meshmanga.com/chapters/301/"},
    ]
    res_3 = infer_chapter_numbers(chapters_case_3)
    print("Case 3 output:")
    for i, c in enumerate(res_3):
        print(f"  Item {i}, Num: {c['chapter_number']}")
    # Chronological ascending: Chapter 1 (1.0) -> Chapter 4 (4.0) -> Chapter 2 (corrected to 4.01) -> Chapter 3 (corrected to 4.02)
    assert abs(res_3[0]["chapter_number"] - 4.02) < 0.001
    assert abs(res_3[1]["chapter_number"] - 4.01) < 0.001
    assert abs(res_3[2]["chapter_number"] - 4.0) < 0.001
    assert abs(res_3[3]["chapter_number"] - 1.0) < 0.001

    print("ALL TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_inference_logic()
