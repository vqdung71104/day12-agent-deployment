# Plan Artifact: `plans/06-lab-complete-production-agent.md`

## Summary
Implement the final project inside `06-lab-complete/`, not a new top-level app, because the existing checker and README are already scoped there. Create `plans/` first, then save this plan as `plans/06-lab-complete-production-agent.md`.

Primary choices locked in:
- Deploy target: Railway
- LLM backend: OpenRouter using `google/gemini-2.5-flash-lite`
- State model: fully stateless app instances, all conversation/rate/cost state in Redis
- Streaming: not required for v1; keep architecture compatible with adding SSE later

## Phases

### Phase 1: Baseline structure and config hardening
- Keep the implementation under `06-lab-complete/` and treat the current files there as the working baseline.
- Replace OpenAI-centric config with OpenRouter-centric config in the settings layer.
- Standardize environment variables to: `HOST`, `PORT`, `ENVIRONMENT`, `LOG_LEVEL`, `REDIS_URL`, `AGENT_API_KEY`, `RATE_LIMIT_PER_MINUTE`, `MONTHLY_BUDGET_USD`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `CONVERSATION_TTL_SECONDS`, `MAX_HISTORY_MESSAGES`.
- Set defaults to: `RATE_LIMIT_PER_MINUTE=10`, `MONTHLY_BUDGET_USD=10.0`, `OPENROUTER_MODEL=google/gemini-2.5-flash-lite`, `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`.
- Validate config on startup and fail fast if `AGENT_API_KEY`, `REDIS_URL`, or `OPENROUTER_API_KEY` are missing in production.
- Update `.env.example` to match the final variable set and remove outdated daily-budget/OpenAI-first wording.

### Phase 2: API contract and Redis-backed runtime
- Keep three public endpoints only: `GET /health`, `GET /ready`, `POST /ask`.
- Make `POST /ask` accept JSON body:
  - `user_id: str`
  - `question: str`
  - `conversation_id: str | null` where null defaults to `user_id`
- Make `POST /ask` return JSON:
  - `user_id`
  - `conversation_id`
  - `answer`
  - `model`
  - `history_length`
  - `usage` with `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`
- Use FastAPI lifespan to initialize and close one Redis client and one async HTTP client cleanly.
- Store conversation history in Redis only, using keys `conv:{user_id}:{conversation_id}`.
- Persist history as ordered JSON messages, trim to `MAX_HISTORY_MESSAGES` after each append, and apply TTL with `CONVERSATION_TTL_SECONDS`.
- Build the OpenRouter call with OpenAI-compatible `/chat/completions` payload using the Redis history plus the new user message.
- Use the usage numbers returned by OpenRouter for post-call accounting; do not introduce a tokenizer dependency for v1.

### Phase 3: Auth, rate limiting, cost guard, and logging
- Authenticate every `POST /ask` request with header `X-API-Key`; return `401` for missing or invalid keys.
- Treat `user_id` from the request body as the identity for rate limiting, budget tracking, and conversation storage. The API key authenticates the client; it does not replace per-user accounting.
- Implement Redis sliding-window rate limiting with a sorted set key `rate:{user_id}`.
- On each request: remove entries older than 60 seconds, count remaining entries, reject the 11th request in a 60-second window with `429`, and include `Retry-After` plus standard rate-limit headers.
- Implement monthly cost guard with Redis hash key `cost:{YYYY-MM}:{user_id}`.
- Check current month spend before the model call; if already at or above `MONTHLY_BUDGET_USD`, reject with `402`.
- After a successful model response, add actual token usage and cost to the monthly record. If a single allowed request crosses the limit, serve that response and block subsequent requests.
- Emit structured JSON logs for startup, shutdown, readiness changes, each request, each rate-limit rejection, each budget rejection, and each model call.
- Include at least these log fields: `ts`, `level`, `event`, `method`, `path`, `status`, `duration_ms`, `user_id`, `conversation_id`, and `error` when relevant.
- Add explicit `SIGTERM` handling in the main app module so readiness flips false during shutdown and the checker sees graceful shutdown support.

### Phase 4: Containerization and local production topology
- Keep a multi-stage `Dockerfile` using a slim Python base, builder stage for dependencies, runtime stage with a non-root user, `PYTHONUNBUFFERED=1`, and a health check against `/health`.
- Keep image contents minimal: app code, shared utils if needed, and installed site packages only.
- Expand `docker-compose.yml` to three services: `agent`, `redis`, `nginx`.
- Expose only Nginx to the host; keep agent internal to the Docker network.
- Add an Nginx config that proxies `/health`, `/ready`, and `/ask` to the `agent` service so `docker compose up --scale agent=3` can load-balance across replicas.
- Add a Redis volume so state survives Redis container restarts during local testing.
- Keep Railway as the only deployment target that must actually work; keep `render.yaml` present and non-conflicting, but do not spend time making Render the validated path unless extra time remains.

### Phase 5: Validation and deployment
- Verify locally through Nginx:
  - `GET /health` returns `200`
  - `GET /ready` returns `200` when Redis is healthy and `503` when Redis is unavailable
  - `POST /ask` returns `401` without `X-API-Key`
  - `POST /ask` returns `200` with valid key and body
  - repeated calls with the same `user_id` and `conversation_id` preserve history across requests
  - the 11th request within one minute for the same `user_id` returns `429`
  - a user above `$10` monthly usage returns `402`
- Run the provided `06-lab-complete/check_production_ready.py` without modifying its expectations.
- Deploy `06-lab-complete/` to Railway with managed Redis and required env vars set there.
- Use Railway’s public URL as the final deliverable URL; Railway ingress replaces the local Nginx role in cloud deployment.

## Public Interfaces and Important Changes
- `POST /ask` becomes a body-driven API with both `user_id` and `question`; `conversation_id` is optional.
- Config changes from daily-budget/OpenAI defaults to monthly-budget/OpenRouter defaults.
- State handling changes from in-memory demo behavior to Redis-only persistence for conversations, rate limit windows, and monthly spend.
- Cloud deployment contract is Railway-first, with env-driven runtime only.

## Test Plan
- Config validation fails fast when production secrets or Redis/OpenRouter settings are missing.
- Redis disconnect causes `/ready` to fail but does not break `/health`.
- Same conversation continues correctly when requests hit different agent replicas.
- Invalid API key never reaches rate limiting or model call logic.
- Rate limit is isolated per `user_id`; one user hitting limit does not block another.
- Budget is isolated per user and resets on month boundary using UTC month keys.
- Shutdown via `SIGTERM` stops readiness first, then closes clients without stack traces.
- Docker image builds successfully as multi-stage and runs as non-root.
- Railway deployment starts with the Dockerfile and passes `/health`.

## Assumptions
- `plans/` does not exist yet; create it when leaving Plan Mode and store this plan there.
- `06-lab-complete/` is the implementation target because the checker is coupled to that folder.
- One shared `AGENT_API_KEY` is sufficient for service authentication; per-user limits are based on body `user_id`.
- OpenRouter usage fields are the source of truth for billing records.
- Streaming is deferred until after the baseline passes the checker and Railway deployment is live.
