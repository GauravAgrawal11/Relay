"""
eval_retrieval.py: Evaluates retrieval quality across Settings A, B, and C.
Computes Hit Rate @ K, Mean Reciprocal Rank (MRR), latency, and Comparison Guard
activations using eval/questions.json. Directly produces the retrieval quality
portion of Table 3 for the final report.
"""

import time
from typing import Any, Dict, List
import pandas as pd

from chunking import RETRIEVAL_SETTINGS
from eval_loader import EvalQuestion, load_eval_questions
from retrieval import HybridRetriever, get_retriever


def evaluate_retriever_on_questions(
    retriever: HybridRetriever,
    questions: List[EvalQuestion],
) -> Dict[str, Any]:
    """Evaluate a single retriever setting against all questions."""
    answerable = [q for q in questions if q.category in ("answerable", "trap_distractor", "trap_multi_paper")]
    hits = 0
    reciprocal_ranks: List[float] = 0
    rr_list: List[float] = []
    latencies_ms: List[float] = []
    guard_activations = 0

    for q in answerable:
        t0 = time.perf_counter()
        retrieved, info = retriever.retrieve(q.question)
        latency = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(latency)

        if info.get("guard_triggered"):
            guard_activations += 1

        # Check if any gold passage matches a retrieved passage
        found_rank = 0
        for rank, p in enumerate(retrieved, start=1):
            for gp in q.gold_passages:
                if gp.matches_page(p.short_tag, p.page):
                    found_rank = rank
                    break
            if found_rank > 0:
                break

        if found_rank > 0:
            hits += 1
            rr_list.append(1.0 / found_rank)
        else:
            rr_list.append(0.0)

    total_ans = len(answerable)
    hit_rate = (hits / total_ans) * 100.0 if total_ans > 0 else 0.0
    mrr = (sum(rr_list) / total_ans) if total_ans > 0 else 0.0
    avg_latency = (sum(latencies_ms) / len(latencies_ms)) if latencies_ms else 0.0

    return {
        "setting": retriever.config.name,
        "chunk_size": retriever.config.chunk_size_words,
        "overlap": retriever.config.overlap_words,
        "top_k": retriever.config.top_k,
        "use_vectors": retriever.config.use_vectors,
        "total_evaluated": total_ans,
        "hits": hits,
        "hit_rate_pct": round(hit_rate, 1),
        "mrr": round(mrr, 3),
        "guard_activations": guard_activations,
        "avg_latency_ms": round(avg_latency, 1),
    }


def run_table3_retrieval_benchmark() -> pd.DataFrame:
    """Run benchmark across all 3 settings and return summary DataFrame."""
    questions = load_eval_questions()
    results = []

    print("Running Table 3 Retrieval Quality Benchmark...")
    for s_name, config in RETRIEVAL_SETTINGS.items():
        print(f"  Evaluating {s_name} ({config.name})...")
        retriever = get_retriever(s_name)
        res = evaluate_retriever_on_questions(retriever, questions)
        results.append(res)

    df = pd.DataFrame(results)
    return df


if __name__ == "__main__":
    df = run_table3_retrieval_benchmark()
    print("\n" + "=" * 80)
    print("TABLE 3: RETRIEVAL QUALITY BENCHMARK (Real Measurements)")
    print("=" * 80)
    print(df.to_string(index=False))
    print("=" * 80)

