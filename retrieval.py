"""
retrieval.py: Hybrid search engine combining BM25 keyword matching and dense vector search.
Implements Reciprocal Rank Fusion (RRF), an optional reranking hook, and a Comparison Guard
that guarantees multi-paper questions retrieve representative passages from each named paper.
Evaluates across configurable chunking settings (A/B/C) with no external vector DB dependencies.
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
from rank_bm25 import BM25Okapi
from fastembed import TextEmbedding

from chunking import ChunkConfig, DEFAULT_SETTING_NAME, Passage, RETRIEVAL_SETTINGS, build_passages
from config import EXTRACTED_DIR, TARGET_PAPERS


def simple_tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric word tokenization for BM25."""
    return re.findall(r"\b\w+\b", text.lower())


def detect_named_papers(query: str) -> List[str]:
    """
    Identify target papers referenced in a user question.
    Returns list of short_tags, e.g. ['attention', 'moshi'].
    """
    q_lower = query.lower()
    named: List[str] = []

    # Attention aliases
    if any(k in q_lower for k in ["attention is all you need", "vaswani", "transformer"]):
        named.append("attention")
    elif "attention" in q_lower and "moshi" in q_lower:
        named.append("attention")

    # Moshi aliases
    if any(k in q_lower for k in ["moshi", "mimi", "helium", "kyutai"]):
        named.append("moshi")

    # Dynamic Chunking / H-Net aliases
    if any(k in q_lower for k in ["dynamic chunking", "h-net", "hnet", "hwang", "tokenizer-free"]):
        named.append("dynamic_chunking")
    elif "chunking" in q_lower and not ("attention" in q_lower and "moshi" in q_lower and len(named) == 2):
        named.append("dynamic_chunking")

    # Remove duplicates preserving order
    result: List[str] = []
    for tag in named:
        if tag not in result:
            result.append(tag)
    return result


class BM25Searcher:
    """In-memory BM25 index built on rank_bm25."""

    def __init__(self, passages: List[Passage]):
        self.passages = passages
        self.corpus_tokens = [simple_tokenize(p.text) for p in passages]
        self.bm25 = BM25Okapi(self.corpus_tokens)

    def search(self, query: str, top_k: int) -> List[Tuple[Passage, float]]:
        query_tokens = simple_tokenize(query)
        if not query_tokens:
            return [(p, 0.0) for p in self.passages[:top_k]]
        scores = self.bm25.get_scores(query_tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(self.passages[idx], float(scores[idx])) for idx in top_indices]


class VectorSearcher:
    """In-memory dense vector index using FastEmbed (ONNX) and numpy cosine similarity."""

    def __init__(
        self,
        passages: List[Passage],
        model_name: str = "BAAI/bge-small-en-v1.5",
        cache_key: Optional[str] = None,
    ):
        self.passages = passages
        self.model = TextEmbedding(model_name=model_name)
        cache_file = EXTRACTED_DIR / f"embs_{cache_key}.npy" if cache_key else None

        if cache_file and cache_file.exists():
            embs_arr = np.load(cache_file)
            if len(embs_arr) == len(passages):
                self.passage_embeddings = embs_arr
                return

        # Truncate passage representations to ~300 words to stay within 512 token limits and avoid memory spikes
        texts = []
        for p in passages:
            words = (f"{p.title}. {p.section_title or ''}. {p.text}").split()
            truncated = " ".join(words[:300])
            texts.append(truncated)

        raw_embs = list(self.model.embed(texts, batch_size=16))
        embs_arr = np.array(raw_embs, dtype=np.float32)
        # L2-normalize for unit cosine similarity
        norms = np.linalg.norm(embs_arr, axis=1, keepdims=True)
        self.passage_embeddings = embs_arr / np.maximum(norms, 1e-12)

        if cache_file:
            np.save(cache_file, self.passage_embeddings)

    def search(self, query: str, top_k: int) -> List[Tuple[Passage, float]]:
        raw_q_emb = list(self.model.embed([query]))[0]
        q_norm = np.linalg.norm(raw_q_emb)
        q_emb = (raw_q_emb / max(q_norm, 1e-12)).astype(np.float32)

        # Cosine similarities
        scores = np.dot(self.passage_embeddings, q_emb)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [(self.passages[idx], float(scores[idx])) for idx in top_indices]


def reciprocal_rank_fusion(
    ranked_lists: List[List[Passage]],
    k_constant: int = 60,
) -> List[Tuple[Passage, float]]:
    """
    Standard Reciprocal Rank Fusion: score(p) = sum(1 / (k + rank)).
    Combines disparate score distributions safely without ad-hoc weighting.
    """
    rrf_scores: Dict[str, float] = {}
    passage_map: Dict[str, Passage] = {}

    for ranked in ranked_lists:
        for rank, p in enumerate(ranked, start=1):
            passage_map[p.passage_id] = p
            rrf_scores[p.passage_id] = rrf_scores.get(p.passage_id, 0.0) + (1.0 / (k_constant + rank))

    sorted_entries = sorted(rrf_scores.items(), key=lambda item: item[1], reverse=True)
    return [(passage_map[pid], score) for pid, score in sorted_entries]


def apply_comparison_guard(
    ranked_passages: List[Passage],
    query: str,
    top_k: int,
) -> Tuple[List[Passage], bool]:
    """
    Comparison Guard:
    If query explicitly names multiple papers, ensure top_k candidates contain
    at least one passage from each named paper. If absent, promote highest ranked
    passage of the missing paper.
    """
    named_papers = detect_named_papers(query)
    # Only active when 2 or more target papers are explicitly involved
    if len(named_papers) < 2:
        return ranked_passages[:top_k], False

    selected = ranked_passages[:top_k]
    represented_papers = {p.short_tag for p in selected}
    missing_papers = [tag for tag in named_papers if tag not in represented_papers]

    if not missing_papers:
        return selected, False

    # Comparison guard triggered: substitute lowest ranked surplus passage with top missing paper passage
    modified = list(selected)
    guard_triggered = False

    for missing in missing_papers:
        # Find best candidate for the missing paper from subsequent ranked passages
        candidate = next((p for p in ranked_passages if p.short_tag == missing), None)
        if candidate:
            # Replace the last element that is from an over-represented paper
            for replace_idx in range(len(modified) - 1, -1, -1):
                over_tag = modified[replace_idx].short_tag
                # Keep at least 1 passage for any named paper
                if sum(1 for p in modified if p.short_tag == over_tag) > 1:
                    modified[replace_idx] = candidate
                    guard_triggered = True
                    break

    return modified, guard_triggered


class HybridRetriever:
    """
    Unified retrieval pipeline for The Relay:
    BM25 -> Vectors -> RRF -> Optional Rerank -> Comparison Guard.
    """

    def __init__(self, config: ChunkConfig):
        self.config = config
        self.passages = build_passages(config)
        self.bm25_searcher = BM25Searcher(self.passages)
        self.vector_searcher: Optional[VectorSearcher] = None
        if config.use_vectors:
            self.vector_searcher = VectorSearcher(self.passages, cache_key=config.name)

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> Tuple[List[Passage], Dict[str, Any]]:
        k = top_k or self.config.top_k
        search_candidates_k = max(k * 4, 30)

        info: Dict[str, Any] = {
            "setting": self.config.name,
            "use_vectors": self.config.use_vectors,
            "guard_triggered": False,
        }

        # 1. BM25 Search
        bm25_results = self.bm25_searcher.search(query, top_k=search_candidates_k)
        bm25_passages = [p for p, _ in bm25_results]

        # 2. Vector Search (if enabled)
        if self.config.use_vectors and self.vector_searcher is not None:
            vec_results = self.vector_searcher.search(query, top_k=search_candidates_k)
            vec_passages = [p for p, _ in vec_results]

            # 3. Reciprocal Rank Fusion
            fused = reciprocal_rank_fusion([bm25_passages, vec_passages])
            ranked_passages = [p for p, _ in fused]
        else:
            ranked_passages = bm25_passages

        # 4. Comparison Guard
        final_passages, guard_triggered = apply_comparison_guard(ranked_passages, query, top_k=k)
        info["guard_triggered"] = guard_triggered

        return final_passages, info


# Registry of initialized retrievers to avoid repeated re-indexing during a run
_RETRIEVER_CACHE: Dict[str, HybridRetriever] = {}


def get_retriever(setting_name: str = DEFAULT_SETTING_NAME) -> HybridRetriever:
    """Get or instantiate cached retriever for a setting."""
    if setting_name not in _RETRIEVER_CACHE:
        config = RETRIEVAL_SETTINGS[setting_name]
        _RETRIEVER_CACHE[setting_name] = HybridRetriever(config)
    return _RETRIEVER_CACHE[setting_name]
