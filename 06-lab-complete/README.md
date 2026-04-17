# Lab 12 — Complete Production Agent

Kết hợp TẤT CẢ những gì đã học trong 1 project hoàn chỉnh.

## Checklist Deliverable

- [x] Dockerfile (multi-stage, < 500 MB)
- [x] docker-compose.yml (agent + redis)
- [x] .dockerignore
- [x] Health check endpoint (`GET /health`)
- [x] Readiness endpoint (`GET /ready`)
- [x] API Key authentication
- [x] Rate limiting
- [x] Cost guard
- [x] Config từ environment variables
- [x] Structured logging
- [x] Graceful shutdown
- [x] Public URL ready (Railway / Render config)

---

## Cấu Trúc

```
06-lab-complete/
├── app/
│   ├── main.py         # Entry point — kết hợp tất cả
│   ├── config.py       # 12-factor config
│   ├── auth.py         # API Key + JWT
│   ├── rate_limiter.py # Rate limiting
│   └── cost_guard.py   # Budget protection
├── Dockerfile          # Multi-stage, production-ready
├── docker-compose.yml  # Full stack
├── railway.toml        # Deploy Railway
├── render.yaml         # Deploy Render
├── .env.example        # Template
├── .dockerignore
└── requirements.txt
```

---

## Chạy Local

```bash
# 1. Setup
cp .env.example .env

# 2. Chạy local production topology
docker compose up --build --scale agent=3

# 3. Test qua Nginx load balancer
curl http://localhost/health
curl http://localhost/ready

# 4. Lấy API key từ .env, test endpoint
API_KEY=$(grep AGENT_API_KEY .env | cut -d= -f2)
curl -H "X-API-Key: $API_KEY" \
     -X POST http://localhost/ask \
     -H "Content-Type: application/json" \
     -d '{"user_id": "user1", "question": "What is deployment?", "conversation_id": "user1"}'
```

`agent` không còn expose trực tiếp ra host. Toàn bộ traffic local đi qua `nginx` trên `http://localhost`, còn state được giữ trong Redis volume `redis-data`.

## Environment Variables

Current config contract:

```env
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=development
LOG_LEVEL=INFO
APP_NAME=Production AI Agent
APP_VERSION=1.0.0
REDIS_URL=redis://localhost:6379/0
AGENT_API_KEY=change-me-in-production
RATE_LIMIT_PER_MINUTE=10
MONTHLY_BUDGET_USD=10.0
OPENROUTER_API_KEY=
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
CONVERSATION_TTL_SECONDS=86400
MAX_HISTORY_MESSAGES=20
ALLOWED_ORIGINS=http://localhost:3000
```

---

## Deploy Railway (< 5 phút)

```bash
# Cài Railway CLI
npm i -g @railway/cli

# Login và deploy
railway login
railway init
railway variables set OPENROUTER_API_KEY=...
railway variables set AGENT_API_KEY=your-secret-key
railway variables set REDIS_URL=redis://...
railway up

# Nhận public URL!
railway domain
```

---

## Deploy Render

1. Push repo lên GitHub
2. Render Dashboard → New → Blueprint
3. Connect repo → Render đọc `render.yaml`
4. Set secrets: `OPENROUTER_API_KEY`, `AGENT_API_KEY`, `REDIS_URL`
5. Deploy → Nhận URL!

---

## Kiểm Tra Production Readiness

```bash
python check_production_ready.py
```

Script này kiểm tra tất cả items trong checklist và báo cáo những gì còn thiếu.
