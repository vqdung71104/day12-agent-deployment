# Deployment Information

## Public URL
https://pleasing-freedom-production-ca8a.up.railway.app

## Platform
Railway

## Service Summary
- App directory deployed: `06-lab-complete`
- Runtime: Dockerfile-based deployment
- Backing store: Railway Redis
- Environment: `production`

## Test Commands

### Health Check
```bash
curl https://pleasing-freedom-production-ca8a.up.railway.app/health
```

Expected:
```json
{
  "status": "ok"
}
```

### Readiness Check
```bash
curl https://pleasing-freedom-production-ca8a.up.railway.app/ready
```

Expected:
```json
{
  "ready": true
}
```

### API Test (with authentication)
```bash
curl -X POST https://pleasing-freedom-production-ca8a.up.railway.app/ask \
  -H "X-API-Key: YOUR_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","question":"Hello","conversation_id":"test"}'
```

Expected behavior:
- Returns `200 OK`
- Returns a JSON response with:
  - `user_id`
  - `conversation_id`
  - `answer`
  - `model`
  - `history_length`
  - `usage`

### Authentication Check
```bash
curl -X POST https://pleasing-freedom-production-ca8a.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","question":"Hello"}'
```

Expected behavior:
- Returns `401 Unauthorized`

### Rate Limit Check
```bash
for i in {1..15}; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST https://pleasing-freedom-production-ca8a.up.railway.app/ask \
    -H "X-API-Key: YOUR_AGENT_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"rate-test","question":"Hello","conversation_id":"rate-test"}'
done
```

Expected behavior:
- After enough requests inside one minute, the API returns `429 Too Many Requests`

## Environment Variables Set
- `ENVIRONMENT`
- `LOG_LEVEL`
- `APP_NAME`
- `APP_VERSION`
- `REDIS_URL`
- `AGENT_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `OPENROUTER_BASE_URL`
- `RATE_LIMIT_PER_MINUTE`
- `MONTHLY_BUDGET_USD`
- `CONVERSATION_TTL_SECONDS`
- `MAX_HISTORY_MESSAGES`
- `ALLOWED_ORIGINS`

## Notes
- Swagger docs are disabled by default in production.
- To enable `/docs` in production, set:

```env
ENABLE_DOCS=true
```

- The final application passes the local readiness checker:

```bash
cd 06-lab-complete
python check_production_ready.py
```

Result:
- `20/20` checks passed
- `100%` production ready

## Screenshots
![running](service-running.png)

