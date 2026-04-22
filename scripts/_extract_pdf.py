import sys
import fitz

src = r"C:/Users/keita/App/ADC/snap-controller/reference/hhD8A3.pdf"
dst = r"C:/Users/keita/App/ADC/snap-controller/reference/hhD8A3_pymupdf.txt"
doc = fitz.open(src)
with open(dst, "w", encoding="utf-8") as f:
    for i, page in enumerate(doc):
        f.write(f"=== Page {i+1} ===\n")
        f.write(page.get_text())
        f.write("\n")
print("done, pages:", len(doc))
