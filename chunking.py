"""
chunking.py: Segments extracted paper pages into structured, retrieval-ready passages.
Implements configurable chunking (piece size, word overlap, top-k) across three
standard evaluation settings. Tracks paper section headings and constructs strict citation
tags ([short_tag version s<sec> p.<page>]), falling back to page numbers when unnumbered.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from config import PAGES_JSONL_PATH, TARGET_PAPERS


@dataclass(frozen=True)
class ChunkConfig:
    """Configurable settings for chunking and retrieval comparison."""
    name: str
    chunk_size_words: int
    overlap_words: int
    top_k: int
    use_vectors: bool = True
    use_rerank: bool = False


# Three predefined comparison settings required for assignment benchmarks
SETTING_A = ChunkConfig(
    name="setting_a_bm25_small",
    chunk_size_words=200,
    overlap_words=40,
    top_k=5,
    use_vectors=False,
    use_rerank=False,
)

SETTING_B = ChunkConfig(
    name="setting_b_hybrid_default",
    chunk_size_words=350,
    overlap_words=70,
    top_k=5,
    use_vectors=True,
    use_rerank=False,
)

SETTING_C = ChunkConfig(
    name="setting_c_large_context",
    chunk_size_words=500,
    overlap_words=100,
    top_k=8,
    use_vectors=True,
    use_rerank=False,
)

RETRIEVAL_SETTINGS: Dict[str, ChunkConfig] = {
    "setting_a": SETTING_A,
    "setting_b": SETTING_B,
    "setting_c": SETTING_C,
}

DEFAULT_SETTING_NAME = "setting_b"


@dataclass
class Passage:
    """A discrete searchable unit of text with provenance metadata."""
    passage_id: str
    paper_id: str
    arxiv_id: str
    version: str
    title: str
    short_tag: str
    page: int
    section: Optional[str]
    section_title: Optional[str]
    tag: str
    text: str
    word_count: int
    char_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_section_in_block(
    block_text: str, next_block_preview: str = ""
) -> Tuple[Optional[str], Optional[str]]:
    """
    Detect section number and title from a text block.
    Returns (section_id, section_title), e.g. ('s3.2.1', 'Scaled Dot-Product Attention')
    or (None, None) if not a section heading.
    """
    lines = [l.strip() for l in block_text.split("\n") if l.strip()]
    if not lines:
        return None, None
    first_line = lines[0]

    # Pattern 1: Numbered heading on same line, e.g. '3.2.1 Scaled Dot-Product Attention'
    # Must be standard integer section numbers (1, 3.2, 3.2.1), each part <= 20
    m_num_title = re.match(r"^([1-9]\d*(?:\.[1-9]\d*)*)\s+([A-Z][A-Za-z0-9\s\-:,/]{2,60})$", first_line)
    if m_num_title:
        sec_num, sec_title = m_num_title.group(1), m_num_title.group(2).strip()
        parts = [int(p) for p in sec_num.split(".")]
        # Genuine academic sections don't have parts > 25 (e.g. 2.80 is a metric, not a section)
        if any(p > 25 for p in parts):
            return None, None
        # Reject table rows or model names with parens or arithmetic
        if any(ch in sec_title for ch in ["(", ")", "=", "+", "↓", "↑", "±", "×"]):
            return None, None
        # Filter out common metric units accidentally matching (e.g. '12.5 Hz', '100 ms')
        if not any(unit in sec_title.lower() for unit in ["hz", "ms", "flops", "percent", "tokens", "acc", "val"]):
            return f"s{sec_num}", sec_title

    # Pattern 2: Dotted section number on line 1, title on line 2
    # Must have a legitimate capitalized title on line 2 or next block preview
    if re.match(r"^([1-9]\d*(?:\.[1-9]\d*)+)$", first_line):
        sec_num = first_line
        parts = [int(p) for p in sec_num.split(".")]
        if any(p > 25 for p in parts):
            return None, None

        def _is_valid_section_title(t: str) -> bool:
            if not (3 <= len(t) < 60 and t[0].isupper()):
                return False
            # Reject math, table formulas, models with parens
            if any(ch in t for ch in ["(", ")", "=", "+", "↓", "↑", "±", "×"]):
                return False
            if re.match(r"^[\d\.\s%x\(\)\-\+]+$", t):
                return False
            if any(w in t.lower() for w in ["hz", "ms", "flops", "percent", "tokens", "acc", "val", "table"]):
                return False
            return True

        if len(lines) > 1 and _is_valid_section_title(lines[1]):
            return f"s{sec_num}", lines[1].strip()
        elif next_block_preview and _is_valid_section_title(next_block_preview):
            return f"s{sec_num}", next_block_preview.strip()
        # If no valid title follows, this is an isolated table metric or number, NOT a heading
        return None, None

    # Pattern 3: Standard unnumbered academic sections -> return None for section id (page-only tag fallback)
    m_kw = re.match(
        r"^(Abstract|Introduction|Background|Model Architecture|Model|Evaluation|Conclusion|References|Appendix(?:\s+[A-Z])?)$",
        first_line,
        re.IGNORECASE,
    )
    if m_kw:
        kw = m_kw.group(1).title()
        return None, kw

    return None, None


def make_citation_tag(short_tag: str, version: str, page: int, section: Optional[str]) -> str:
    """
    Construct citation tag following the assignment specification:
    e.g. '[attention v7 s3.2 p.4]' or '[attention v7 p.4]' when section is absent.
    """
    if section:
        return f"[{short_tag} {version} {section} p.{page}]"
    return f"[{short_tag} {version} p.{page}]"


def chunk_paper_pages(
    paper_pages: List[Dict[str, Any]],
    config: ChunkConfig,
) -> List[Passage]:
    """
    Chunk extracted pages for a single paper while tracking page provenance
    and active section headers.
    """
    if not paper_pages:
        return []

    passages: List[Passage] = []
    chunk_words: List[str] = []
    current_page = paper_pages[0]["page_number"]
    current_section: Optional[str] = None
    current_section_title: Optional[str] = None
    chunk_index = 0

    paper_id = paper_pages[0]["paper_id"]
    arxiv_id = paper_pages[0]["arxiv_id"]
    version = paper_pages[0]["version"]
    title = paper_pages[0]["title"]
    short_tag = paper_pages[0]["short_tag"]

    for p_idx, page_data in enumerate(paper_pages):
        page_num = page_data["page_number"]
        blocks = [b.strip() for b in page_data["text"].split("\n\n") if b.strip()]

        for b_idx, block in enumerate(blocks):
            next_preview = blocks[b_idx + 1].split("\n")[0].strip() if b_idx + 1 < len(blocks) else ""
            detected_sec, detected_title = detect_section_in_block(block, next_preview)
            if detected_sec:
                current_section = detected_sec
                current_section_title = detected_title

            block_words = block.split()
            if not block_words:
                continue

            # If this is the start of a new chunk, record its anchor page
            if not chunk_words:
                current_page = page_num

            chunk_words.extend(block_words)

            # When chunk reaches configured size, finalize a passage
            while len(chunk_words) >= config.chunk_size_words:
                passage_words = chunk_words[:config.chunk_size_words]
                passage_text = " ".join(passage_words)

                tag = make_citation_tag(short_tag, version, current_page, current_section)
                p = Passage(
                    passage_id=f"{short_tag}_{version}_p{current_page}_c{chunk_index}",
                    paper_id=paper_id,
                    arxiv_id=arxiv_id,
                    version=version,
                    title=title,
                    short_tag=short_tag,
                    page=current_page,
                    section=current_section,
                    section_title=current_section_title,
                    tag=tag,
                    text=passage_text,
                    word_count=len(passage_words),
                    char_count=len(passage_text),
                )
                passages.append(p)
                chunk_index += 1

                # Slide window forward by (chunk_size_words - overlap_words)
                step = max(1, config.chunk_size_words - config.overlap_words)
                chunk_words = chunk_words[step:]
                # Update anchor page for the next overlap window
                current_page = page_num

    # Emit any remaining words if non-trivial
    if chunk_words:
        passage_text = " ".join(chunk_words)
        tag = make_citation_tag(short_tag, version, current_page, current_section)
        p = Passage(
            passage_id=f"{short_tag}_{version}_p{current_page}_c{chunk_index}",
            paper_id=paper_id,
            arxiv_id=arxiv_id,
            version=version,
            title=title,
            short_tag=short_tag,
            page=current_page,
            section=current_section,
            section_title=current_section_title,
            tag=tag,
            text=passage_text,
            word_count=len(chunk_words),
            char_count=len(passage_text),
        )
        passages.append(p)

    return passages


def build_passages(
    config: ChunkConfig,
    pages_path: Path = PAGES_JSONL_PATH,
) -> List[Passage]:
    """
    Build a unified index of passages across all target papers for a given ChunkConfig.
    """
    if not pages_path.exists():
        raise FileNotFoundError(f"Extracted pages not found at {pages_path}. Run ingest.py first.")

    pages_by_paper: Dict[str, List[Dict[str, Any]]] = {}
    with open(pages_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                item = json.loads(line)
                pid = item["paper_id"]
                pages_by_paper.setdefault(pid, []).append(item)

    all_passages: List[Passage] = []
    for full_id in TARGET_PAPERS.keys():
        paper_pages = pages_by_paper.get(full_id, [])
        paper_passages = chunk_paper_pages(paper_pages, config)
        all_passages.extend(paper_passages)

    return all_passages


if __name__ == "__main__":
    print("Testing passage construction across all 3 settings...")
    for s_name, s_cfg in RETRIEVAL_SETTINGS.items():
        passages = build_passages(s_cfg)
        sec_count = sum(1 for p in passages if p.section is not None)
        print(f"[{s_name}] total passages: {len(passages)} | with section tag: {sec_count} ({sec_count/len(passages)*100:.1f}%)")
        print(f"  Sample passage 0 tag: {passages[0].tag}")
        print(f"  Sample passage mid tag: {passages[len(passages)//2].tag}")

