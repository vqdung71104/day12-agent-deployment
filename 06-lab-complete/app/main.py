"""
Production AI Agent

Phase 2 focuses on:
  - Redis-backed conversation history
  - OpenRouter chat completions integration
  - One shared Redis client + HTTP client per process
  - Readiness based on Redis availability
"""
from __future__ import annotations

import json
import logging
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import redis.asyncio as redis
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_usage
from app.rate_limiter import check_rate_limit


logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}',
)
logger = logging.getLogger(__name__)

START_TIME = time.time()
_is_ready = False
_request_count = 0
_error_count = 0


def log_event(level: int, event: str, **fields: Any) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": logging.getLevelName(level),
        "event": event,
        **fields,
    }
    logger.log(level, json.dumps(payload))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def conversation_key(user_id: str, conversation_id: str) -> str:
    return f"conv:{user_id}:{conversation_id}"


def normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        return "".join(text_parts).strip()
    return str(content or "")


def mock_answer(question: str, history: list[dict[str, str]]) -> str:
    turns = len([msg for msg in history if msg.get("role") == "user"])
    return (
        f"Mock response for: {question}\n"
        f"Conversation context loaded: {turns} prior user messages."
    )


async def load_history(redis_client: redis.Redis, user_id: str, conversation_id: str) -> list[dict[str, str]]:
    raw = await redis_client.get(conversation_key(user_id, conversation_id))
    if not raw:
        return []
    try:
        history = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(history, list):
        return []

    normalized_history: list[dict[str, str]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant", "system"} and isinstance(content, str):
            normalized_history.append({"role": role, "content": content})
    return normalized_history[-settings.max_history_messages :]


async def save_history(
    redis_client: redis.Redis,
    user_id: str,
    conversation_id: str,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    trimmed_history = history[-settings.max_history_messages :]
    await redis_client.set(
        conversation_key(user_id, conversation_id),
        json.dumps(trimmed_history),
        ex=settings.conversation_ttl_seconds,
    )
    return trimmed_history


async def call_openrouter(
    http_client: httpx.AsyncClient,
    user_id: str,
    conversation_id: str,
    messages: list[dict[str, str]],
) -> tuple[str, str, dict[str, Any]]:
    if not settings.openrouter_api_key:
        answer = mock_answer(messages[-1]["content"], messages[:-1])
        prompt_tokens = max(1, sum(len(msg["content"].split()) for msg in messages))
        completion_tokens = max(1, len(answer.split()))
        return answer, f"mock/{settings.openrouter_model}", {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": 0.0,
        }

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://railway.app",
        "X-Title": settings.app_name,
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": messages,
        "user": user_id,
        "session_id": conversation_id,
    }

    response = await http_client.post("/chat/completions", headers=headers, json=payload)
    if response.status_code >= 400:
        detail = response.text[:500]
        log_event(
            logging.ERROR,
            "openrouter_error",
            status=response.status_code,
            user_id=user_id,
            conversation_id=conversation_id,
            error=detail,
        )
        raise HTTPException(status_code=502, detail="LLM provider request failed")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise HTTPException(status_code=502, detail="LLM provider returned no choices")

    message = choices[0].get("message") or {}
    answer = normalize_message_content(message.get("content"))
    usage = data.get("usage") or {}
    model = data.get("model") or settings.openrouter_model
    return answer, model, usage


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_ready

    app.state.redis = None
    app.state.http_client = httpx.AsyncClient(
        base_url=settings.openrouter_base_url.rstrip("/"),
        timeout=httpx.Timeout(30.0, connect=10.0),
    )
    app.state.readiness_error = None

    log_event(
        logging.INFO,
        "startup",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )

    if settings.redis_url:
        try:
            app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
            await app.state.redis.ping()
            _is_ready = True
            log_event(logging.INFO, "ready", redis_url=settings.redis_url)
        except Exception as exc:
            _is_ready = False
            app.state.readiness_error = str(exc)
            log_event(logging.ERROR, "redis_connect_failed", error=str(exc))
    else:
        _is_ready = False
        app.state.readiness_error = "REDIS_URL not configured"
        log_event(logging.WARNING, "redis_missing", error=app.state.readiness_error)

    try:
        yield
    finally:
        _is_ready = False
        if app.state.http_client is not None:
            await app.state.http_client.aclose()
        if app.state.redis is not None:
            await app.state.redis.aclose()
        log_event(logging.INFO, "shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    global _request_count, _error_count
    start = time.time()
    _request_count += 1
    request.state.user_id = None
    request.state.conversation_id = None
    try:
        response: Response = await call_next(request)
    except Exception as exc:
        _error_count += 1
        log_event(
            logging.ERROR,
            "request_error",
            method=request.method,
            path=request.url.path,
            user_id=getattr(request.state, "user_id", None),
            conversation_id=getattr(request.state, "conversation_id", None),
            error=str(exc),
        )
        raise

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if "server" in response.headers:
        del response.headers["server"]

    duration_ms = round((time.time() - start) * 1000, 1)
    log_event(
        logging.INFO,
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
        user_id=getattr(request.state, "user_id", None),
        conversation_id=getattr(request.state, "conversation_id", None),
    )
    return response


class AskRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    question: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=128)


class UsageResponse(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class AskResponse(BaseModel):
    user_id: str
    conversation_id: str
    answer: str
    model: str
    history_length: int
    usage: UsageResponse


@app.post("/ask", response_model=AskResponse, tags=["Agent"])
async def ask_agent(
    body: AskRequest,
    response: Response,
    request: Request,
    _key: str = Depends(verify_api_key),
):
    if not _is_ready or request.app.state.redis is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    conversation_id = body.conversation_id or body.user_id
    redis_client: redis.Redis = request.app.state.redis
    http_client: httpx.AsyncClient = request.app.state.http_client
    request.state.user_id = body.user_id
    request.state.conversation_id = conversation_id

    try:
        rate_limit_info = await check_rate_limit(redis_client, body.user_id)
    except HTTPException as exc:
        log_event(
            logging.WARNING,
            "rate_limit_rejected",
            user_id=body.user_id,
            conversation_id=conversation_id,
            error=str(exc.detail),
        )
        raise

    response.headers["X-RateLimit-Limit"] = str(rate_limit_info["limit"])
    response.headers["X-RateLimit-Remaining"] = str(rate_limit_info["remaining"])
    response.headers["X-RateLimit-Reset"] = str(rate_limit_info["reset_at"])

    try:
        await check_budget(redis_client, body.user_id)
    except HTTPException as exc:
        log_event(
            logging.WARNING,
            "budget_rejected",
            user_id=body.user_id,
            conversation_id=conversation_id,
            error=str(exc.detail),
        )
        raise

    history = await load_history(redis_client, body.user_id, conversation_id)
    messages = history + [{"role": "user", "content": body.question}]

    log_event(
        logging.INFO,
        "agent_call",
        user_id=body.user_id,
        conversation_id=conversation_id,
        history_length=len(history),
        backend="mock" if not settings.openrouter_api_key else "openrouter",
    )

    answer, model, usage = await call_openrouter(
        http_client=http_client,
        user_id=body.user_id,
        conversation_id=conversation_id,
        messages=messages,
    )

    final_history = await save_history(
        redis_client=redis_client,
        user_id=body.user_id,
        conversation_id=conversation_id,
        history=messages + [{"role": "assistant", "content": answer}],
    )

    usage_payload = UsageResponse(
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        total_tokens=int(usage.get("total_tokens", 0) or 0),
        cost_usd=float(usage.get("cost", 0.0) or 0.0),
    )

    budget_usage = await record_usage(
        redis_client,
        body.user_id,
        prompt_tokens=usage_payload.prompt_tokens,
        completion_tokens=usage_payload.completion_tokens,
        total_tokens=usage_payload.total_tokens,
        cost_usd=usage_payload.cost_usd,
    )

    log_event(
        logging.INFO,
        "usage_recorded",
        user_id=body.user_id,
        conversation_id=conversation_id,
        cost_usd=usage_payload.cost_usd,
        month=budget_usage["month"],
        monthly_cost_usd=budget_usage["cost_usd"],
        rate_limit_remaining=rate_limit_info["remaining"],
    )

    return AskResponse(
        user_id=body.user_id,
        conversation_id=conversation_id,
        answer=answer,
        model=model,
        history_length=len(final_history),
        usage=usage_payload,
    )


@app.get("/health", tags=["Operations"])
def health():
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "total_requests": _request_count,
        "checks": {
            "redis_configured": bool(settings.redis_url),
            "llm": "openrouter" if settings.openrouter_api_key else "mock",
        },
        "timestamp": utc_now_iso(),
    }


@app.get("/ready", tags=["Operations"])
async def ready(request: Request):
    if not _is_ready or request.app.state.redis is None:
        raise HTTPException(status_code=503, detail=request.app.state.readiness_error or "Not ready")

    try:
        await request.app.state.redis.ping()
    except Exception as exc:
        request.app.state.readiness_error = str(exc)
        raise HTTPException(status_code=503, detail="Redis not available") from exc

    return {"ready": True, "timestamp": utc_now_iso()}


def _handle_signal(signum, _frame):
    global _is_ready
    _is_ready = False
    log_event(logging.INFO, "signal", signum=signum)


signal.signal(signal.SIGTERM, _handle_signal)


if __name__ == "__main__":
    log_event(logging.INFO, "boot", host=settings.host, port=settings.port)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        timeout_graceful_shutdown=30,
    )
