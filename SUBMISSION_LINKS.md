# The Relay — Official Submission Links

This document contains the primary public URLs for the evaluation and review of **The Relay** assignment submission.

---

### 1. Source Code Repository (GitHub)
- **URL:** [https://github.com/GauravAgrawal11/Relay](https://github.com/GauravAgrawal11/Relay)
- **Description:** Complete, zero-framework Python implementation featuring:
  - 3-Zone production-gated RAG pipeline (Pre-Gate, Gate, Post-Gate).
  - Pure Python BM25 + FastEmbed ONNX hybrid vector retrieval with Comparison Guards.
  - Token-bucket sliding-window rate limiter enforcing Groq's 8,000 TPM ceiling.
  - Multi-turn conversational manager backed by SQLite in WAL mode.
  - 46 automated unit and regression tests passing with 100% success.

---

### 2. Full AI Assistant Conversation History (Claude)
- **URL:** [https://claude.ai/share/e1a3182f-4589-4971-ba55-0cb3b6a65237](https://claude.ai/share/e1a3182f-4589-4971-ba55-0cb3b6a65237)
- **Description:** Real, uncleaned conversation history exported directly from Claude as required by Section 12 of the brief:
  - Captures the three key architectural prompts that shaped the pipeline.
  - Documents catching the Unicode lenticular bracket bug (`【...】`) from `openai/gpt-oss-120b`.
  - Details overruling the assistant to avoid external agent frameworks (Hard Rule 5).
  - Demonstrates the iterative engineering process behind all 4 canonical benchmark tables.

---

### 3. Submission Package Files in This Repository
- **PDF Report (Exactly 2 pages):** [`The_Relay_Submission_Report.pdf`](The_Relay_Submission_Report.pdf)
- **Raw Measurements Log (122 requests):** [`results.csv`](results.csv)
- **Sub-millisecond Event Audit Log:** [`events.jsonl`](events.jsonl)
- **Local Markdown Conversation Archive:** [`CHAT_HISTORY.md`](CHAT_HISTORY.md)
