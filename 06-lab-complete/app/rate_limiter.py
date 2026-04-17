from __future__ import annotations

import time
import uuid

from fastapi import HTTPException

from app.config import settings


WINDOW_SECONDS = 60


async def check_rate_limit(redis_client, user_id: str) -> dict[str, int | str]:
    key = f"rate:{user_id}"
    now = time.time()
    window_start = now - WINDOW_SECONDS

    await redis_client.zremrangebyscore(key, 0, window_start)
    current_count = await redis_client.zcard(key)

    if current_count >= settings.rate_limit_per_minute:
        oldest_entries = await redis_client.zrange(key, 0, 0, withscores=True)
        retry_after = WINDOW_SECONDS
        reset_at = int(now) + WINDOW_SECONDS
        if oldest_entries:
            oldest_score = float(oldest_entries[0][1])
            retry_after = max(1, int(oldest_score + WINDOW_SECONDS - now) + 1)
            reset_at = int(oldest_score + WINDOW_SECONDS)

        headers = {
            "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(reset_at),
            "Retry-After": str(retry_after),
        }
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": settings.rate_limit_per_minute,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": retry_after,
            },
            headers=headers,
        )

    member = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    await redis_client.zadd(key, {member: now})
    await redis_client.expire(key, WINDOW_SECONDS)

    remaining = max(0, settings.rate_limit_per_minute - (current_count + 1))
    reset_at = int(now) + WINDOW_SECONDS
    return {
        "limit": settings.rate_limit_per_minute,
        "remaining": remaining,
        "reset_at": reset_at,
        "retry_after": WINDOW_SECONDS,
    }
