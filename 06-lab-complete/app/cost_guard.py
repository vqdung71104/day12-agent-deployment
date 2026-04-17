from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from app.config import settings


def current_month_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def cost_key(user_id: str) -> str:
    return f"cost:{current_month_utc()}:{user_id}"


async def get_usage(redis_client, user_id: str) -> dict[str, float | int | str]:
    raw = await redis_client.hgetall(cost_key(user_id))
    if not raw:
        return {
            "month": current_month_utc(),
            "request_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

    return {
        "month": current_month_utc(),
        "request_count": int(raw.get("request_count", 0) or 0),
        "prompt_tokens": int(raw.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(raw.get("completion_tokens", 0) or 0),
        "total_tokens": int(raw.get("total_tokens", 0) or 0),
        "cost_usd": float(raw.get("cost_usd", 0.0) or 0.0),
    }


async def check_budget(redis_client, user_id: str) -> dict[str, float | int | str]:
    usage = await get_usage(redis_client, user_id)
    if float(usage["cost_usd"]) >= settings.monthly_budget_usd:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "Monthly budget exceeded",
                "used_usd": float(usage["cost_usd"]),
                "budget_usd": settings.monthly_budget_usd,
                "month": usage["month"],
            },
        )
    return usage


async def record_usage(
    redis_client,
    user_id: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_usd: float,
) -> dict[str, float | int | str]:
    key = cost_key(user_id)
    month = current_month_utc()

    pipeline = redis_client.pipeline()
    pipeline.hincrby(key, "request_count", 1)
    pipeline.hincrby(key, "prompt_tokens", int(prompt_tokens))
    pipeline.hincrby(key, "completion_tokens", int(completion_tokens))
    pipeline.hincrby(key, "total_tokens", int(total_tokens))
    pipeline.hincrbyfloat(key, "cost_usd", float(cost_usd))
    pipeline.hset(key, mapping={"month": month, "updated_at": datetime.now(timezone.utc).isoformat()})
    pipeline.expire(key, 60 * 60 * 24 * 62)
    await pipeline.execute()

    return await get_usage(redis_client, user_id)
