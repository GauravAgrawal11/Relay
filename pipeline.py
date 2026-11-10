"""
pipeline.py: End-to-end execution pipeline uniting Zones 1, 2, and 3 for The Relay.
Implements local pre-filtering, spell-fixing, hybrid retrieval, evidence gating,
concurrency/rate-limited model dispatch, citation validation, and automated grading.
All results are appended directly to results.csv and events.jsonl.
"""

import asyncio
from dataclasses import dataclass
import datetime
import time
from typing import Any, Dict, List, Optional
import uuid

from chunking import DEFAULT_SETTING_NAME, Passage
from config import DEFAULT_GROQ_MODEL
from eval_loader import EvalQuestion
from evidence import (
    check_clarity,
    check_evidence,
    spell_fix_query,
    validate_citations,
)
from grading import grade_answer
from limiter import TrafficGate, default_gate
from logger import (
    EventRecord,
    ResultRecord,
    default_event_logger,
    default_results_logger,
)
from model_client import (
    BaseModelClient,
    ModelResponse,
    ModelTiming,
    ModelUsage,
    create_model_client,
)
from retrieval import HybridRetriever, get_retriever


@dataclass
class PipelineResult:
    """Consolidated end-to-end response and provenance metadata."""
    request_id: str
    scenario: str
    source: str
    original_query: str
    normalized_query: str
    answer: str
    status: str
    timing: ModelTiming
    usage: ModelUsage
    retrieval_setting: str
    retrieved_passages: List[Passage]
    citations_valid: bool
    answer_grade: str


class RelayPipeline:
    """
    Main processing pipeline executing user queries through Zones 1 to 3.
    """

    def __init__(
        self,
        source: str = "real",
        retrieval_setting: str = DEFAULT_SETTING_NAME,
        model_name: str = DEFAULT_GROQ_MODEL,
        gate: Optional[TrafficGate] = None,
        validate_before_stream: bool = False,
    ):
        self.source = source
        self.retrieval_setting = retrieval_setting
        self.model_name = model_name
        self.gate = gate or default_gate
        self.validate_before_stream = validate_before_stream
        self.retriever: HybridRetriever = get_retriever(retrieval_setting)
        self.client: BaseModelClient = create_model_client(
            source=source,
            model_name=model_name,
            mock_standin=(source == "stand-in"),
        )

    def _build_generation_prompt(self, query: str, passages: List[Passage]) -> List[Dict[str, str]]:
        system_msg = (
            "You are a precise technical assistant for engineers answering questions about three arXiv papers:\n"
            "- Attention Is All You Need (1706.03762v7) [attention v7]\n"
            "- Moshi: a speech-text foundation model for real-time dialogue (2410.00037v1) [moshi v1]\n"
            "- Dynamic Chunking for End-to-End Hierarchical Sequence Modeling (2507.07955v2) [dynamic_chunking v2]\n\n"
            "STRICT RULES:\n"
            "1. Answer ONLY using the retrieved passages below. Never guess or hallucinate.\n"
            "2. Every claim or factual statement MUST cite its provenance with the exact tag format like [attention v7 s3.2 p.4].\n"
            "3. If the retrieved passages do not contain enough evidence to answer, explicitly state: 'The provided papers do not establish this.'\n"
        )

        passages_text = "\n\n".join(
            f"Passage {idx+1} {p.tag}:\n{p.text}"
            for idx, p in enumerate(passages)
        )

        user_msg = f"Retrieved Passages:\n{passages_text}\n\nQuestion: {query}"
        return [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

    async def answer(
        self,
        question: str,
        scenario: str = "interactive",
        request_id: Optional[str] = None,
        eval_question: Optional[EvalQuestion] = None,
    ) -> PipelineResult:
        req_id = request_id or f"req_{uuid.uuid4().hex[:8]}"
        start_time_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        t0 = time.time()

        # ==========================================
        # ZONE 1: Pre-Gate Local Processing
        # ==========================================
        # 1. Normalize and spell-fix query
        fixed_query, _ = spell_fix_query(question)
        normalized_query = fixed_query.strip()

        # 2. Clarity check
        clarity = check_clarity(normalized_query)
        if clarity == "INCOMPLETE":
            refusal_ans = "Please provide more details in your question regarding Attention, Moshi, or Dynamic Chunking."
            timing = ModelTiming(sent_time=t0, first_token_time=time.time(), done_time=time.time())
            usage = ModelUsage(prompt_tokens=len(question.split()), completion_tokens=len(refusal_ans.split()), total_tokens=len(question.split()) + len(refusal_ans.split()))
            grade = "refused_correctly" if (eval_question and eval_question.must_refuse) else "refused_wrongly"

            res = PipelineResult(
                request_id=req_id,
                scenario=scenario,
                source=self.source,
                original_query=question,
                normalized_query=normalized_query,
                answer=refusal_ans,
                status="ok",
                timing=timing,
                usage=usage,
                retrieval_setting=self.retrieval_setting,
                retrieved_passages=[],
                citations_valid=True,
                answer_grade=grade,
            )
            self._log_final_result(res, start_time_iso)
            return res

        # 3. Hybrid Retrieval
        retrieval_start = time.time()
        passages, ret_info = self.retriever.retrieve(normalized_query)
        retrieval_ms = (time.time() - retrieval_start) * 1000.0

        default_event_logger.log(EventRecord(
            request_id=req_id,
            step="retrieval",
            start_time=retrieval_start,
            end_time=time.time(),
            duration_ms=retrieval_ms,
            extra=ret_info,
        ))

        # 4. Evidence check
        ev_decision = check_evidence(normalized_query, passages)
        decision = ev_decision["decision"]

        # Local refusal path: Zero Groq API calls!
        if decision == "UNRELATED":
            refusal_ans = "This assistant covers only Attention Is All You Need, Moshi, and Dynamic Chunking."
            timing = ModelTiming(sent_time=t0, first_token_time=time.time(), done_time=time.time())
            usage = ModelUsage(prompt_tokens=len(question.split()), completion_tokens=len(refusal_ans.split()), total_tokens=len(question.split()) + len(refusal_ans.split()))
            grade = "refused_correctly" if (eval_question and eval_question.must_refuse) else "wrong"

            res = PipelineResult(
                request_id=req_id,
                scenario=scenario,
                source=self.source,
                original_query=question,
                normalized_query=normalized_query,
                answer=refusal_ans,
                status="ok",
                timing=timing,
                usage=usage,
                retrieval_setting=self.retrieval_setting,
                retrieved_passages=passages,
                citations_valid=True,
                answer_grade=grade,
            )
            self._log_final_result(res, start_time_iso)
            return res

        # Query rewrite loop on weak evidence (once only)
        if decision == "WEAK_PARTIAL":
            # Attempt single rewrite by appending key terms
            rewritten_query = f"{normalized_query} details"
            rewritten_passages, _ = self.retriever.retrieve(rewritten_query)
            ev2 = check_evidence(rewritten_query, rewritten_passages)
            if ev2["decision"] == "SUFFICIENT":
                passages = rewritten_passages
                normalized_query = rewritten_query
            else:
                # Still weak: return evidence-based refusal with closest excerpts
                closest_tags = " ".join(p.tag for p in passages[:2])
                refusal_ans = f"The provided papers don't establish this fact directly. Related passages: {closest_tags}"
                timing = ModelTiming(sent_time=t0, first_token_time=time.time(), done_time=time.time())
                usage = ModelUsage(prompt_tokens=len(question.split()), completion_tokens=len(refusal_ans.split()), total_tokens=len(question.split()) + len(refusal_ans.split()))
                grade = "refused_correctly" if (eval_question and eval_question.must_refuse) else "refused_wrongly"

                res = PipelineResult(
                    request_id=req_id,
                    scenario=scenario,
                    source=self.source,
                    original_query=question,
                    normalized_query=normalized_query,
                    answer=refusal_ans,
                    status="ok",
                    timing=timing,
                    usage=usage,
                    retrieval_setting=self.retrieval_setting,
                    retrieved_passages=passages,
                    citations_valid=True,
                    answer_grade=grade,
                )
                self._log_final_result(res, start_time_iso)
                return res

        # ==========================================
        # ZONE 2: Dispatch Across Traffic Gate
        # ==========================================
        messages = self._build_generation_prompt(normalized_query, passages)
        model_resp: ModelResponse = await self.gate.execute_through_gate(
            client=self.client,
            messages=messages,
            request_id=req_id,
            validate_before_stream=self.validate_before_stream,
        )

        # ==========================================
        # ZONE 3: Post-Gate Validation & Logging
        # ==========================================
        answer = model_resp.content
        citations_valid = True
        if model_resp.status == "ok":
            valid, cite_errors = validate_citations(answer, passages)
            citations_valid = valid
            if not valid:
                # Fallback on invalid citation: replace with safe refusal
                answer = "The retrieved passages do not provide verifiable evidence to support this claim."

        # Compute grade
        if eval_question:
            grade = grade_answer(
                answer=answer,
                question=eval_question,
                citations_valid=citations_valid,
                status=model_resp.status,
            )
        else:
            if model_resp.status in ("rate_limited", "timeout"):
                grade = "refused_correctly"
            elif citations_valid and len(answer.split()) > 5:
                grade = "correct"
            else:
                grade = "wrong"

        res = PipelineResult(
            request_id=req_id,
            scenario=scenario,
            source=self.source,
            original_query=question,
            normalized_query=normalized_query,
            answer=answer,
            status=model_resp.status,
            timing=model_resp.timing,
            usage=model_resp.usage,
            retrieval_setting=self.retrieval_setting,
            retrieved_passages=passages,
            citations_valid=citations_valid,
            answer_grade=grade,
        )

        self._log_final_result(res, start_time_iso)
        return res

    def _log_final_result(self, res: PipelineResult, start_time_iso: str) -> None:
        """Write single row to results.csv strictly matching the 12-column schema."""
        rec = ResultRecord(
            request_id=res.request_id,
            scenario=res.scenario,
            source=res.source,
            start_time=start_time_iso,
            first_token_ms=res.timing.first_token_ms,
            total_ms=res.timing.total_ms,
            status=res.status,
            retries=0,
            tokens_in=res.usage.prompt_tokens,
            tokens_out=res.usage.completion_tokens,
            retrieval_setting=res.retrieval_setting,
            answer_grade=res.answer_grade,
        )
        default_results_logger.log_result(rec)
