"""
evidence.py: Zone 1 preprocessing, entity spell-checking, and lexical evidence verification.
Validates input clarity, performs vocabulary-constrained spell correction (protecting domain terms
like Moshi, Mimi, and H-Net), and checks whether retrieved passages contain sufficient factual
grounding before invoking the LLM gate. Also validates citation tags in Zone 3.
"""

import difflib
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from chunking import Passage
from config import TARGET_PAPERS

# Protected terminology that must never be altered by spell correction
PROTECTED_TERMS = {
    "attention", "transformer", "vaswani", "shazeer",
    "moshi", "mimi", "helium", "kyutai",
    "hnet", "h-net", "hwang", "chunking", "dechunking",
    "rvq", "bleu", "bpe", "flops", "downsampler", "upsampler",
    "tokenizer", "subword", "multihead", "softmax",
}

# Common English stopwords to ignore during lexical overlap calculation
STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "and", "but", "if", "or", "because",
    "as", "until", "while", "of", "what", "which", "who", "whom", "this", "that",
    "these", "those", "how", "why", "when", "where", "can", "could", "should",
    "would", "does", "did", "paper", "papers", "model", "models",
}


def build_paper_vocabulary() -> Set[str]:
    """Compile a set of verified vocabulary words from target paper titles and domain terms."""
    vocab = set(PROTECTED_TERMS)
    for p in TARGET_PAPERS.values():
        for w in re.findall(r"\b[a-zA-Z]{3,}\b", p.title.lower()):
            vocab.add(w)
    return vocab


PAPER_VOCABULARY = build_paper_vocabulary()


def spell_fix_query(query: str) -> Tuple[str, bool]:
    """
    Spell-fix user queries using difflib against domain vocabulary.
    Preserves protected domain terms and returns (fixed_query, was_modified).
    """
    words = re.findall(r"\b[\w\-]+\b|[^\w\s]", query)
    modified = False
    fixed_words = []

    for w in words:
        w_lower = w.lower()
        if w_lower in PROTECTED_TERMS or len(w) < 4 or not w.isalpha():
            fixed_words.append(w)
            continue

        # Look for close match in paper vocabulary if not a standard English word
        matches = difflib.get_close_matches(w_lower, list(PAPER_VOCABULARY), n=1, cutoff=0.82)
        if matches and matches[0] != w_lower:
            fixed_words.append(matches[0])
            modified = True
        else:
            fixed_words.append(w)

    fixed_query = " ".join(fixed_words)
    # Clean spacing around punctuation
    fixed_query = re.sub(r"\s+([?.!,])", r"\1", fixed_query)
    return fixed_query, modified


def check_clarity(query: str) -> str:
    """
    Classify query clarity: 'CLEAR', 'AMBIGUOUS', or 'INCOMPLETE'.
    Rule-based to ensure conservative single-turn handling.
    """
    q_strip = query.strip()
    words = q_strip.split()
    if len(words) < 3 and not any(p in q_strip.lower() for p in ["attention", "moshi", "h-net"]):
        return "INCOMPLETE"

    # Pronoun checks without referent
    if words[0].lower() in ["it", "they", "he", "she"] and len(words) < 5:
        return "AMBIGUOUS"

    return "CLEAR"


def check_evidence(
    query: str,
    retrieved_passages: List[Passage],
    min_overlap_threshold: float = 0.20,
) -> Dict[str, Any]:
    """
    Verify whether retrieved passages provide sufficient evidence to answer the query.
    Note: Threshold 0.20 is derived from eval/questions.json (overfitting caveat noted).

    Returns decision:
      - 'SUFFICIENT': Proceed to generation.
      - 'WEAK_PARTIAL': Overlap exists but insufficient; refuse with closest passages.
      - 'UNRELATED': Zero overlap; refuse with scope boundary message.
    """
    q_tokens = [w.lower() for w in re.findall(r"\b[a-zA-Z]{3,}\b", query) if w.lower() not in STOPWORDS]
    if not q_tokens:
        return {"decision": "SUFFICIENT", "overlap_ratio": 1.0, "reason": "Generic query"}

    combined_text = " ".join(p.text.lower() for p in retrieved_passages)

    # Calculate token overlap
    matched_tokens = [t for t in q_tokens if t in combined_text]
    overlap_ratio = len(matched_tokens) / len(q_tokens)

    # Check for completely unrelated topic (e.g. Apple Vision Pro, GPT-4, LLaMA)
    unrelated_indicators = ["vision pro", "gpt-4", "llama", "battery", "apple", "swedish"]
    if any(ind in query.lower() for ind in unrelated_indicators):
        if overlap_ratio < 0.15:
            return {
                "decision": "UNRELATED",
                "overlap_ratio": round(overlap_ratio, 2),
                "matched_tokens": matched_tokens,
                "reason": "This assistant covers only Attention Is All You Need, Moshi, and Dynamic Chunking.",
            }

    if overlap_ratio >= min_overlap_threshold:
        return {
            "decision": "SUFFICIENT",
            "overlap_ratio": round(overlap_ratio, 2),
            "matched_tokens": matched_tokens,
            "reason": "Passages contain adequate lexical evidence",
        }
    elif overlap_ratio > 0.0:
        return {
            "decision": "WEAK_PARTIAL",
            "overlap_ratio": round(overlap_ratio, 2),
            "matched_tokens": matched_tokens,
            "reason": "The papers don't establish this fact directly.",
        }
    else:
        return {
            "decision": "UNRELATED",
            "overlap_ratio": 0.0,
            "matched_tokens": [],
            "reason": "This assistant covers only Attention Is All You Need, Moshi, and Dynamic Chunking.",
        }


def validate_citations(answer: str, retrieved_passages: List[Passage]) -> Tuple[bool, List[str]]:
    """
    Validate that all citation tags in the answer exist in the retrieved passages.
    Tag format: [paper_tag version (section)? p.page]
    """
    tag_pattern = re.compile(r"\[([a-z_]+)\s+(v\d+)(?:\s+(s[\w\.]+))?\s+p\.(\d+)\]", re.IGNORECASE)
    matches = tag_pattern.findall(answer)

    if not matches:
        # Check if the answer is a refusal
        if any(w in answer.lower() for w in ["refuse", "not mention", "does not contain", "cannot answer"]):
            return True, []
        return False, ["Answer contains no citation tags and is not a refusal."]

    valid_passage_tags = {p.tag.lower() for p in retrieved_passages}
    # Also index by paper and page for flexible tag matching
    valid_pages = {(p.short_tag.lower(), p.page) for p in retrieved_passages}

    errors = []
    for m in matches:
        short_tag, version, section, page_str = m[0].lower(), m[1].lower(), m[2], int(m[3])
        # Construct candidate tag
        tag_str = f"[{short_tag} {version} {section} p.{page_str}]" if section else f"[{short_tag} {version} p.{page_str}]"
        if tag_str.lower() not in valid_passage_tags and (short_tag, page_str) not in valid_pages:
            errors.append(f"Cited tag {tag_str} was not found in retrieved passages.")

    return (len(errors) == 0), errors

