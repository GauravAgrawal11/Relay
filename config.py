"""
config.py: Central configuration for The Relay.
Defines filesystem paths, the exact metadata for the three target arXiv papers,
schema definitions for results.csv, and environment variable loaders.
All subsequent modules import paths and settings from this file.
"""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Load local .env if present (never committed to git)
load_dotenv()

# --- Filesystem Paths ---
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / "data"
RAW_PAPERS_DIR: Path = DATA_DIR / "raw"
EXTRACTED_DIR: Path = DATA_DIR / "extracted"
PAGES_JSONL_PATH: Path = EXTRACTED_DIR / "pages.jsonl"
MANIFEST_PATH: Path = EXTRACTED_DIR / "manifest.json"

DB_PATH: Path = DATA_DIR / "relay.db"
RESULTS_CSV_PATH: Path = BASE_DIR / "results.csv"
EVENT_LOG_PATH: Path = BASE_DIR / "events.jsonl"

EVAL_DIR: Path = BASE_DIR / "eval"
QUESTIONS_PATH: Path = EVAL_DIR / "questions.json"

STANDIN_DIR: Path = BASE_DIR / "standin"
PROFILE_PATH: Path = STANDIN_DIR / "profile.json"
PROBES_DIR: Path = BASE_DIR / "probes"


# --- Paper Specifications ---
@dataclass(frozen=True)
class PaperMeta:
    """Metadata specification for each target arXiv paper."""
    arxiv_id: str
    version: str
    title: str
    short_tag: str
    pdf_url: str
    abs_url: str

    @property
    def full_id(self) -> str:
        return f"{self.arxiv_id}{self.version}"

    @property
    def filename(self) -> str:
        return f"{self.full_id}.pdf"


# Exactly three target papers as required by assignment specification
TARGET_PAPERS: Dict[str, PaperMeta] = {
    "1706.03762v7": PaperMeta(
        arxiv_id="1706.03762",
        version="v7",
        title="Attention Is All You Need",
        short_tag="attention",
        pdf_url="https://arxiv.org/pdf/1706.03762v7.pdf",
        abs_url="https://arxiv.org/abs/1706.03762v7",
    ),
    "2410.00037v1": PaperMeta(
        arxiv_id="2410.00037",
        version="v1",
        title="Moshi: a speech-text foundation model for real-time dialogue",
        short_tag="moshi",
        pdf_url="https://arxiv.org/pdf/2410.00037v1.pdf",
        abs_url="https://arxiv.org/abs/2410.00037v1",
    ),
    "2507.07955v2": PaperMeta(
        arxiv_id="2507.07955",
        version="v2",
        title="Dynamic Chunking for End-to-End Hierarchical Sequence Modeling",
        short_tag="dynamic_chunking",
        pdf_url="https://arxiv.org/pdf/2507.07955v2.pdf",
        abs_url="https://arxiv.org/abs/2507.07955v2",
    ),
}


# --- Evaluation and Results Schema ---
RESULTS_CSV_COLUMNS: List[str] = [
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade",
]

ALLOWED_SOURCES = ("real", "stand-in")
ALLOWED_STATUSES = ("ok", "rate_limited", "timeout", "error")
ALLOWED_ANSWER_GRADES = ("correct", "wrong", "refused_correctly", "refused_wrongly")


# --- Model Configuration ---
# Default to active Groq model verified on the account; overridable via GROQ_MODEL env var
DEFAULT_GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


# --- Helper Functions ---
def ensure_directories() -> None:
    """Create essential project directories if they do not exist."""
    for directory in [DATA_DIR, RAW_PAPERS_DIR, EXTRACTED_DIR, EVAL_DIR, STANDIN_DIR, PROBES_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


def get_groq_api_key(required: bool = False) -> Optional[str]:
    """
    Retrieve GROQ_API_KEY strictly from the environment.
    Never hardcoded or written to disk.
    """
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key and required:
        raise ValueError(
            "GROQ_API_KEY environment variable is not set. "
            "Please export GROQ_API_KEY or define it in your local .env file."
        )
    return key if key else None

