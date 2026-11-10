"""
ingest.py: Downloads exact arXiv target papers, verifies arXiv version stamps,
computes SHA-256 digests and file sizes, and extracts per-page text into pages.jsonl.
This forms the immutable, auditable data layer for all downstream chunking,
retrieval, and citation tracking across Attention, Moshi, and Dynamic Chunking.
"""

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import fitz  # PyMuPDF
import httpx

from config import (
    DATA_DIR,
    RAW_PAPERS_DIR,
    EXTRACTED_DIR,
    PAGES_JSONL_PATH,
    MANIFEST_PATH,
    TARGET_PAPERS,
    PaperMeta,
    ensure_directories,
)


def compute_sha256_and_size(file_path: Path) -> Tuple[str, int]:
    """Calculate the SHA-256 hash and byte size of a file."""
    sha256_hash = hashlib.sha256()
    size_bytes = 0
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
            size_bytes += len(chunk)
    return sha256_hash.hexdigest(), size_bytes


def download_paper(paper: PaperMeta, target_path: Path, timeout: float = 90.0, force: bool = False) -> Path:
    """Download an arXiv PDF with a descriptive User-Agent header."""
    if target_path.exists() and not force and target_path.stat().st_size > 0:
        return target_path

    headers = {
        "User-Agent": "TheRelay/1.0 (academic research assignment; contact: student@example.edu)"
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout) as client:
        response = client.get(paper.pdf_url)
        response.raise_for_status()
        with open(target_path, "wb") as f:
            f.write(response.content)

    return target_path


def verify_arxiv_stamp(doc: fitz.Document, expected_full_id: str) -> Tuple[bool, str]:
    """
    Search pages (starting with page 1) for arXiv version stamp.
    e.g. 'arXiv:1706.03762v7' or 'arXiv:2410.00037v1'.
    """
    import re
    stamp_pattern = re.compile(rf"arXiv:{re.escape(expected_full_id)}\b", re.IGNORECASE)
    general_pattern = re.compile(r"arXiv:\d{4}\.\d{4,5}(?:v\d+)?", re.IGNORECASE)

    # Search first 3 pages
    for page_idx in range(min(3, len(doc))):
        page_text = doc[page_idx].get_text("text")
        if stamp_pattern.search(page_text):
            # Extract surrounding stamp text
            matches = [m.group(0) for m in general_pattern.finditer(page_text)]
            return True, matches[0] if matches else f"arXiv:{expected_full_id}"

    return False, "stamp_not_found"


def extract_page_text(page: fitz.Page) -> str:
    """
    Extract text block-by-block with reading-order sorting enabled.
    Separates two-column layouts naturally and drops image/drawing blocks.
    """
    blocks = page.get_text("blocks", sort=True)
    text_blocks: List[str] = []
    for b in blocks:
        # block tuple: (x0, y0, x1, y1, text, block_no, block_type)
        # block_type == 0 is text
        if len(b) >= 7 and b[6] == 0:
            block_text = b[4].strip()
            if block_text:
                text_blocks.append(block_text)
        elif len(b) < 7:
            # Fallback if structure varies
            text_blocks.append(str(b[4]).strip())
    return "\n\n".join(text_blocks)


def ingest_papers(force: bool = False) -> Dict[str, Any]:
    """
    Execute full ingestion:
    1. Ensure directories exist.
    2. Download PDFs to data/raw.
    3. Verify arXiv stamps, sizes, and SHA-256.
    4. Write manifest.json.
    5. Extract per-page text to data/extracted/pages.jsonl.
    """
    ensure_directories()
    manifest_papers: Dict[str, Any] = {}
    pages_records: List[Dict[str, Any]] = []

    for full_id, paper in TARGET_PAPERS.items():
        pdf_path = RAW_PAPERS_DIR / paper.filename
        download_paper(paper, pdf_path, force=force)

        sha256_hash, file_size = compute_sha256_and_size(pdf_path)

        # Open with PyMuPDF
        doc = fitz.open(pdf_path)
        page_count = len(doc)
        verified, stamp_text = verify_arxiv_stamp(doc, paper.full_id)

        manifest_papers[full_id] = {
            "arxiv_id": paper.arxiv_id,
            "version": paper.version,
            "title": paper.title,
            "short_tag": paper.short_tag,
            "filename": paper.filename,
            "size_bytes": file_size,
            "sha256": sha256_hash,
            "page_count": page_count,
            "arxiv_stamp_verified": verified,
            "arxiv_stamp_text": stamp_text,
        }

        # Extract per-page text
        for idx in range(page_count):
            page = doc[idx]
            page_text = extract_page_text(page)
            record = {
                "paper_id": full_id,
                "arxiv_id": paper.arxiv_id,
                "version": paper.version,
                "title": paper.title,
                "short_tag": paper.short_tag,
                "page_number": idx + 1,  # 1-indexed for human citation
                "total_pages": page_count,
                "char_count": len(page_text),
                "word_count": len(page_text.split()),
                "text": page_text,
            }
            pages_records.append(record)

        doc.close()

    # Write manifest.json
    manifest_data = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "papers": manifest_papers,
        "total_pages_all_papers": sum(p["page_count"] for p in manifest_papers.values()),
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # Write pages.jsonl
    with open(PAGES_JSONL_PATH, "w", encoding="utf-8") as f:
        for rec in pages_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return manifest_data


if __name__ == "__main__":
    print("Ingesting target papers...")
    manifest = ingest_papers()
    print("Ingestion complete!")
    for pid, p in manifest["papers"].items():
        print(f"[{pid}] {p['title']}")
        print(f"  Pages: {p['page_count']} | Size: {p['size_bytes']} bytes | Verified Stamp: {p['arxiv_stamp_text']}")
        print(f"  SHA-256: {p['sha256']}")

