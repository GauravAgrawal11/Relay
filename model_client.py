"""
model_client.py: Unified client interface abstracting both Groq and the stand-in service.
Captures fine-grained timestamps (sent, first-byte, first-token, and done), calculates
precise millisecond latencies, parses rate-limit headers (x-ratelimit-*, retry-after),
and tracks token usage while keeping API keys strictly isolated from logs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
import httpx

from config import DEFAULT_GROQ_MODEL, get_groq_api_key
from logger import EventRecord, default_event_logger, sanitize_headers


@dataclass
class ModelTiming:
    """Accurate timing metrics for model invocation."""
    sent_time: float
    first_byte_time: float = 0.0
    first_token_time: float = 0.0
    user_first_token_time: float = 0.0
    done_time: float = 0.0

    @property
    def first_token_ms(self) -> float:
        if self.first_token_time > self.sent_time:
            return (self.first_token_time - self.sent_time) * 1000.0
        return 0.0

    @property
    def user_first_token_ms(self) -> float:
        if self.user_first_token_time > self.sent_time:
            return (self.user_first_token_time - self.sent_time) * 1000.0
        return self.first_token_ms

    @property
    def total_ms(self) -> float:
        if self.done_time > self.sent_time:
            return (self.done_time - self.sent_time) * 1000.0
        return 0.0


@dataclass
class ModelUsage:
    """Token usage counters."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ModelResponse:
    """Standardized response from either Groq or the stand-in backend."""
    content: str
    timing: ModelTiming
    usage: ModelUsage
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    status: str = "ok"  # 'ok', 'rate_limited', 'timeout', 'error'
    error_message: Optional[str] = None
    source: str = "real"  # 'real' or 'stand-in'
    retries: int = 0


class BaseModelClient(ABC):
    """Abstract interface for all model interactions in The Relay."""

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        validate_before_stream: bool = False,
        request_id: str = "req_default",
    ) -> ModelResponse:
        """Execute a chat completion request and return unified ModelResponse."""
        pass


class GroqClient(BaseModelClient):
    """Real Groq API client using free-tier models with full telemetry."""

    def __init__(
        self,
        model_name: str = DEFAULT_GROQ_MODEL,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 30.0,
    ):
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        messages: List[Dict[str, str]],
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        validate_before_stream: bool = False,
        request_id: str = "req_default",
    ) -> ModelResponse:
        api_key = get_groq_api_key(required=True)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TheRelay/1.0",
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        timing = ModelTiming(sent_time=time.time())
        collected_tokens: List[str] = []
        status_code = 0
        raw_headers: Dict[str, str] = {}
        usage = ModelUsage()
        status = "ok"
        error_msg: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                if stream:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        status_code = response.status_code
                        raw_headers = dict(response.headers)
                        timing.first_byte_time = time.time()

                        if status_code != 200:
                            error_body = await response.aread()
                            error_msg = error_body.decode("utf-8", errors="replace")
                            status = "rate_limited" if status_code == 429 else "error"
                        else:
                            first_token_seen = False
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    choices = chunk.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content_piece = delta.get("content", "")
                                        if content_piece:
                                            now = time.time()
                                            if not first_token_seen:
                                                timing.first_token_time = now
                                                if not validate_before_stream:
                                                    timing.user_first_token_time = now
                                                first_token_seen = True
                                            collected_tokens.append(content_piece)
                                    # Parse usage if emitted by provider
                                    if "usage" in chunk and chunk["usage"]:
                                        u = chunk["usage"]
                                        usage.prompt_tokens = u.get("prompt_tokens", 0)
                                        usage.completion_tokens = u.get("completion_tokens", 0)
                                        usage.total_tokens = u.get("total_tokens", 0)
                                except json.JSONDecodeError:
                                    continue
                else:
                    # Non-streamed fallback
                    response = await client.post(url, headers=headers, json=payload)
                    status_code = response.status_code
                    raw_headers = dict(response.headers)
                    timing.first_byte_time = time.time()
                    if status_code == 200:
                        data = response.json()
                        timing.first_token_time = time.time()
                        timing.user_first_token_time = time.time()
                        content = data["choices"][0]["message"]["content"]
                        collected_tokens.append(content)
                        u = data.get("usage", {})
                        usage.prompt_tokens = u.get("prompt_tokens", 0)
                        usage.completion_tokens = u.get("completion_tokens", 0)
                        usage.total_tokens = u.get("total_tokens", 0)
                    else:
                        error_msg = response.text
                        status = "rate_limited" if status_code == 429 else "error"

        except httpx.TimeoutException as exc:
            status = "timeout"
            error_msg = f"Request timed out: {exc}"
        except Exception as exc:
            status = "error"
            error_msg = str(exc)

        timing.done_time = time.time()
        # If user sees first token only after full validation is done:
        if validate_before_stream and timing.user_first_token_time == 0.0:
            timing.user_first_token_time = timing.done_time

        full_content = "".join(collected_tokens)
        if not usage.completion_tokens and full_content:
            usage.completion_tokens = max(1, len(full_content.split()))
        if not usage.prompt_tokens:
            usage.prompt_tokens = sum(len(m.get("content", "").split()) for m in messages)
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        clean_headers = sanitize_headers(raw_headers)
        model_resp = ModelResponse(
            content=full_content,
            timing=timing,
            usage=usage,
            status_code=status_code,
            headers=clean_headers,
            status=status,
            error_message=error_msg,
            source="real",
        )

        # Log event to events.jsonl
        event = EventRecord(
            request_id=request_id,
            step="model_call",
            start_time=timing.sent_time,
            end_time=timing.done_time,
            duration_ms=timing.total_ms,
            headers=clean_headers,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            status=status,
            error_message=error_msg,
            extra={"source": "real", "model": self.model_name},
        )
        default_event_logger.log(event)

        return model_resp


class StandinClient(BaseModelClient):
    """
    Stand-in model client connecting to local FastAPI stand-in server
    or operating in local simulated mode with identical response telemetry.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000/v1",
        timeout_seconds: float = 30.0,
        mock_mode: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.mock_mode = mock_mode

    async def complete(
        self,
        messages: List[Dict[str, str]],
        stream: bool = True,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        validate_before_stream: bool = False,
        request_id: str = "req_standin",
    ) -> ModelResponse:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": "standin-llama-3.3-70b",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        timing = ModelTiming(sent_time=time.time())
        collected_tokens: List[str] = []
        status_code = 200
        raw_headers: Dict[str, str] = {
            "x-ratelimit-limit-requests": "30",
            "x-ratelimit-remaining-requests": "29",
            "x-ratelimit-limit-tokens": "6000",
            "x-ratelimit-remaining-tokens": "5500",
        }
        usage = ModelUsage()
        status = "ok"
        error_msg: Optional[str] = None

        if self.mock_mode:
            # Internal deterministic mock calibrated from real probes (probes/raw_probe_output.json)
            # p50 TTFT = 2442.4 ms, p50 Total = 2877.6 ms (Issue 28)
            timing.first_byte_time = timing.sent_time + 2.100
            timing.first_token_time = timing.sent_time + 2.442
            timing.user_first_token_time = timing.first_token_time if not validate_before_stream else timing.sent_time + 2.878
            simulated_text = (
                "Based on the provided passages [attention v7 s3.2.2 p.4], "
                "the scaled dot-product attention scales the dot product by 1/sqrt(d_k)."
            )
            collected_tokens.append(simulated_text)
            timing.done_time = timing.sent_time + 2.878
            usage.prompt_tokens = sum(len(m.get("content", "").split()) for m in messages)
            usage.completion_tokens = len(simulated_text.split())
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        else:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    if stream:
                        async with client.stream("POST", url, json=payload) as response:
                            status_code = response.status_code
                            raw_headers = dict(response.headers)
                            timing.first_byte_time = time.time()
                            if status_code != 200:
                                error_msg = (await response.aread()).decode("utf-8", errors="replace")
                                status = "rate_limited" if status_code == 429 else "error"
                            else:
                                first_token_seen = False
                                async for line in response.aiter_lines():
                                    line = line.strip()
                                    if not line or not line.startswith("data:"):
                                        continue
                                    data_str = line[5:].strip()
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data_str)
                                        choices = chunk.get("choices", [])
                                        if choices:
                                            delta = choices[0].get("delta", {})
                                            content_piece = delta.get("content", "")
                                            if content_piece:
                                                now = time.time()
                                                if not first_token_seen:
                                                    timing.first_token_time = now
                                                    if not validate_before_stream:
                                                        timing.user_first_token_time = now
                                                    first_token_seen = True
                                                collected_tokens.append(content_piece)
                                        if "usage" in chunk and chunk["usage"]:
                                            u = chunk["usage"]
                                            usage.prompt_tokens = u.get("prompt_tokens", 0)
                                            usage.completion_tokens = u.get("completion_tokens", 0)
                                            usage.total_tokens = u.get("total_tokens", 0)
                                    except json.JSONDecodeError:
                                        continue
                    else:
                        response = await client.post(url, json=payload)
                        status_code = response.status_code
                        raw_headers = dict(response.headers)
                        timing.first_byte_time = time.time()
                        timing.first_token_time = time.time()
                        timing.user_first_token_time = time.time()
                        if status_code == 200:
                            data = response.json()
                            content = data["choices"][0]["message"]["content"]
                            collected_tokens.append(content)
                        else:
                            error_msg = response.text
                            status = "rate_limited" if status_code == 429 else "error"
            except httpx.TimeoutException as exc:
                status = "timeout"
                error_msg = f"Stand-in request timed out: {exc}"
            except Exception as exc:
                status = "error"
                error_msg = str(exc)

            timing.done_time = time.time()

        if validate_before_stream and timing.user_first_token_time == 0.0:
            timing.user_first_token_time = timing.done_time

        full_content = "".join(collected_tokens)
        if not usage.completion_tokens and full_content:
            usage.completion_tokens = max(1, len(full_content.split()))
        if not usage.prompt_tokens:
            usage.prompt_tokens = sum(len(m.get("content", "").split()) for m in messages)
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        clean_headers = sanitize_headers(raw_headers)
        model_resp = ModelResponse(
            content=full_content,
            timing=timing,
            usage=usage,
            status_code=status_code,
            headers=clean_headers,
            status=status,
            error_message=error_msg,
            source="stand-in",
        )

        event = EventRecord(
            request_id=request_id,
            step="model_call",
            start_time=timing.sent_time,
            end_time=timing.done_time,
            duration_ms=timing.total_ms,
            headers=clean_headers,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            status=status,
            error_message=error_msg,
            extra={"source": "stand-in"},
        )
        default_event_logger.log(event)

        return model_resp


def create_model_client(
    source: str = "real",
    model_name: str = DEFAULT_GROQ_MODEL,
    mock_standin: bool = False,
) -> BaseModelClient:
    """Factory creating the appropriate backend client based on runtime configuration."""
    if source == "stand-in":
        return StandinClient(mock_mode=mock_standin)
    elif source == "real":
        return GroqClient(model_name=model_name)
    else:
        raise ValueError(f"Unknown model source '{source}'. Expected 'real' or 'stand-in'.")
