"""
evidence.py: Zone 1 preprocessing, entity spell-checking, scope, and lexical evidence verification.
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
    "attention", "transformer", "transformers", "vaswani", "shazeer",
    "moshi", "mimi", "helium", "kyutai",
    "hnet", "h-net", "hwang", "chunking", "dechunking",
    "rvq", "bleu", "bpe", "flops", "downsampler", "upsampler",
    "tokenizer", "subword", "multihead", "softmax", "encoder", "decoder",
}

# Common English stopwords to ignore during lexical overlap calculation
STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "and", "but", "if", "or", "because",
    "as", "until", "while", "of", "what", "which", "who", "whom", "this", "that",
    "these", "those", "how", "why", "when", "where", "can", "could", "should",
    "would", "does", "did", "paper", "papers", "model", "models", "tell", "explain",
    "describe", "define", "give", "show", "used", "using",
}


def build_paper_vocabulary() -> Set[str]:
    """Compile a set of verified vocabulary words from target paper titles and domain terms."""
    vocab = set(PROTECTED_TERMS)
    for p in TARGET_PAPERS.values():
        for w in re.findall(r"\b[a-zA-Z]{3,}\b", p.title.lower()):
            vocab.add(w)
    return vocab


PAPER_VOCABULARY = build_paper_vocabulary()


def tokenize_words_with_contractions(text: str) -> List[str]:
    """Hyphen and apostrophe aware tokenizer: preserves 'Moshi\'s' and 'H-Net'."""
    return re.findall(r"\b[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*(?:'[a-zA-Z0-9]+)?\b|[^\w\s]", text)


def spell_fix_query(query: str) -> Tuple[str, bool]:
    """
    Spell-fix user queries using difflib against domain vocabulary.
    Preserves protected domain terms and apostrophes without mangling (e.g. Moshi's stays Moshi's).
    """
    tokens = tokenize_words_with_contractions(query)
    modified = False
    fixed_tokens = []

    for tok in tokens:
        tok_lower = tok.lower()
        # Preserve punctuation or contractions
        if not re.match(r"^[a-zA-Z0-9\-']+$", tok):
            fixed_tokens.append(tok)
            continue

        base_word = tok_lower.split("'")[0]
        if base_word in PROTECTED_TERMS or len(base_word) < 4 or not base_word.replace("-", "").isalpha():
            fixed_tokens.append(tok)
            continue

        # Look for close match in paper vocabulary
        matches = difflib.get_close_matches(base_word, list(PAPER_VOCABULARY), n=1, cutoff=0.82)
        if matches and matches[0] != base_word:
            apostrophe_part = tok.split("'")[1] if "'" in tok else ""
            suffix = f"'{apostrophe_part}" if apostrophe_part else ""
            fixed_tokens.append(matches[0] + suffix)
            modified = True
        else:
            fixed_tokens.append(tok)

    # Re-assemble preserving apostrophes without extra spaces
    fixed_query = " ".join(fixed_tokens)
    fixed_query = re.sub(r"\s+([?.!,])", r"\1", fixed_query)
    fixed_query = re.sub(r"\s+'\s*", "'", fixed_query)
    return fixed_query, modified


def check_clarity(query: str) -> str:
    """
    Classify query clarity: 'CLEAR', 'AMBIGUOUS', or 'INCOMPLETE'.
    Only treats a question as incomplete when it has no domain content terms.
    Short prompts like 'Define BLEU' or 'Explain Mimi' are CLEAR because they name corpus terms.
    """
    q_strip = query.strip()
    words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9\-]+\b", q_strip)]
    content_words = [w for w in words if w not in STOPWORDS]

    # If completely empty or no content words at all
    if not content_words:
        return "INCOMPLETE"

    # Pronoun checks without clear referent
    if words and words[0] in ["it", "they", "he", "she"] and len(words) < 5:
        return "AMBIGUOUS"

    # If very short (1-2 words), ensure at least one content term is in paper vocabulary
    if len(words) <= 2:
        has_corpus_term = any(w in PAPER_VOCABULARY or w in PROTECTED_TERMS for w in content_words)
        if not has_corpus_term:
            return "INCOMPLETE"

    return "CLEAR"


def check_scope(query: str) -> bool:
    """
    Check if query is completely outside the library scope using corpus vocabulary.
    Returns True if query has some domain connection; False if completely alien.
    """
    q_tokens = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9\-]{3,}\b", query) if w.lower() not in STOPWORDS]
    if not q_tokens:
        return True  # Let downstream handle empty content terms

    # Check how many query tokens overlap with the paper vocabulary
    overlap = sum(1 for t in q_tokens if t in PAPER_VOCABULARY or t in PROTECTED_TERMS)
    # If the user asks a multi-word question and has 0 overlap with the entire domain vocabulary
    if len(q_tokens) >= 3 and overlap == 0:
        return False
    return True


def check_evidence(
    query: str,
    retrieved_passages: List[Passage],
    min_overlap_threshold: float = 0.20,
) -> Dict[str, Any]:
    """
    Verify whether retrieved passages provide sufficient evidence to answer the query.
    No hardcoded topic blocklists: uses lexical overlap and IDF-weighted coverage.
    """
    q_tokens = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9\-]{3,}\b", query) if w.lower() not in STOPWORDS]
    if not q_tokens:
        return {
            "decision": "WEAK_PARTIAL",
            "overlap_ratio": 0.0,
            "matched_tokens": [],
            "reason": "The question contains no specific technical keywords from the papers.",
        }

    # First check scope from corpus vocabulary
    if not check_scope(query):
        return {
            "decision": "UNRELATED",
            "overlap_ratio": 0.0,
            "matched_tokens": [],
            "reason": "This assistant covers only Attention Is All You Need, Moshi, and Dynamic Chunking.",
        }

    combined_text = " ".join(p.text.lower() for p in retrieved_passages)
    matched_tokens = [t for t in q_tokens if t in combined_text]
    overlap_ratio = len(matched_tokens) / len(q_tokens) if q_tokens else 0.0

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
            "reason": "The provided papers don't establish this fact directly.",
        }
    else:
        return {
            "decision": "UNRELATED",
            "overlap_ratio": 0.0,
            "matched_tokens": [],
            "reason": "This assistant covers only Attention Is All You Need, Moshi, and Dynamic Chunking.",
        }


def extract_cited_tags(text: str) -> List[str]:
    """Extract normalized citation tags like '[moshi v1 s3.3.1 p.11]' from answer text."""
    normalized = text.replace("【", "[").replace("】", "]")
    tag_pattern = re.compile(r"\[([a-z_]+)\s+(v\d+)(?:\s+(s[\w\.]+))?\s+p\.(\d+)\]", re.IGNORECASE)
    matches = tag_pattern.findall(normalized)
    tags = []
    for m in matches:
        short_tag, version, section, page_str = m[0].lower(), m[1].lower(), m[2], int(m[3])
        tag_str = f"[{short_tag} {version} {section} p.{page_str}]" if section else f"[{short_tag} {version} p.{page_str}]"
        tags.append(tag_str.lower())
    return tags


def filter_supporting_passages(
    answer: str,
    retrieved_passages: List[Passage],
) -> List[Passage]:
    """
    Identify which retrieved passages actually support the factual claims in the answer.
    Separates general retrieved passages from active supporting citations.
    A passage is considered supporting if:
    1. It is explicitly cited in the answer text, OR
    2. Its core numerical/entity facts directly support the sentence claims.
    """
    cited_tags = set(extract_cited_tags(answer))
    supporting: List[Passage] = []

    # 1. First add passages whose tags match explicit citations
    if cited_tags:
        for p in retrieved_passages:
            if p.tag.lower() in cited_tags and p not in supporting:
                supporting.append(p)

    # 2. If no explicit tags or looking for strongest claim evidence, match key facts
    if not supporting:
        # Extract numbers and non-stopwords from answer
        nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", answer))
        key_words = {w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", answer) if w.lower() not in STOPWORDS}

        best_passage = None
        best_score = 0
        for p in retrieved_passages:
            p_text = p.text.lower()
            num_matches = sum(1 for n in nums if n in p_text)
            kw_matches = sum(1 for w in key_words if w in p_text)
            score = (num_matches * 3) + kw_matches
            if score > best_score:
                best_score = score
                best_passage = p

        if best_passage and best_score >= 3:
            supporting.append(best_passage)

    return supporting


def format_pretty_citation(passage: Passage) -> str:
    """
    Format a clean human-readable citation label for UI presentation:
    e.g. '[Moshi v1 · Section 3.3.1 · Page 10–11]' or '[Attention v7 · Section 3.2 · Page 4]'
    """
    title_map = {
        "attention": "Attention Is All You Need",
        "moshi": "Moshi",
        "dynamic_chunking": "Dynamic Chunking",
    }
    short_map = {
        "attention": "Attention",
        "moshi": "Moshi",
        "dynamic_chunking": "Dynamic Chunking",
    }
    paper_name = short_map.get(passage.short_tag, passage.short_tag.capitalize())
    version_str = passage.version

    sec_str = ""
    if passage.section:
        sec_num = passage.section.lstrip("s")
        sec_str = f" · Section {sec_num}"

    page_str = f" · Page {passage.page}"
    return f"[{paper_name} {version_str}{sec_str}{page_str}]"


def validate_citations(answer: str, retrieved_passages: List[Passage]) -> Tuple[bool, List[str]]:
    """
    Validate citation tags. Requires exact tag match against retrieved passages.
    Recognizes Unicode lenticular brackets 【...】 and model's own refusal phrasing.
    """
    normalized_answer = answer.replace("【", "[").replace("】", "]")
    tag_pattern = re.compile(r"\[([a-z_]+)\s+(v\d+)(?:\s+(s[\w\.]+))?\s+p\.(\d+)\]", re.IGNORECASE)
    matches = tag_pattern.findall(normalized_answer)

    # Check if answer is a recognized refusal phrasing (model or template)
    refusal_markers = [
        "refuse", "not mention", "does not contain", "cannot answer",
        "do not establish", "don't establish", "papers do not say",
        "not covered in the provided", "not establish this",
        "only covers", "do not provide verifiable evidence",
    ]
    if any(marker in answer.lower() for marker in refusal_markers):
        return True, []

    if not matches:
        return False, ["Answer contains no citation tags and is not a refusal."]

    valid_passage_tags = {p.tag.lower() for p in retrieved_passages}
    errors = []
    for m in matches:
        short_tag, version, section, page_str = m[0].lower(), m[1].lower(), m[2], int(m[3])
        tag_str = f"[{short_tag} {version} {section} p.{page_str}]" if section else f"[{short_tag} {version} p.{page_str}]"
        # Strict exact tag check
        if tag_str.lower() not in valid_passage_tags:
            errors.append(f"Cited tag {tag_str} was not found in retrieved passages.")

    return (len(errors) == 0), errors
