# FairPlay server (FastAPI)

API under `/api`, teacher UI at `/teacher`, student UI at `/student` (the built React app in `static/`).

## Run for development (SQLite, no Redis)
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```
Open http://127.0.0.1:8000/teacher. Demo users: `teacher / teacher123`, students `alice / alice123`, `bob / bob123`, `carol / carol123`.

## Production-shaped (Postgres + Redis + nginx, scalable)
```
export PROCTORA_SECRET=$(openssl rand -hex 32)
docker compose up --build --scale api=4
```

## Settings (environment)
| Variable | Default | |
|---|---|---|
| DATABASE_URL | sqlite+aiosqlite:///./data/proctora.db | `postgresql+asyncpg://user:pw@host/db` in production |
| REDIS_URL | (none: in-memory, single process) | required with more than one worker |
| PROCTORA_SECRET | insecure dev value | token signing key |
| DB_POOL_SIZE / DB_MAX_OVERFLOW | 10 / 20 | per worker |
| PROCTORA_SEED_LOADTEST_USERS | 0 | creates lt0001.. users for the load test |
| DKUOJ_URL / DKUOJ_TOKEN | (none: not connected) | DKU OJ integration (app/integrations/dkuoj, docs/dkuoj-bridge.md); the token is DKU OJ's FAIRPLAY_TOKEN |
| DKUOJ_SYNC_SECONDS / DKUOJ_DELIVERY_RPS | 120 / 10 | sync interval; hand-ins sent to DKU OJ per second |
| FAIRPLAY_PUBLIC_URL | (none) | this server's https address, for the report links sent to integrations (needed: DKU OJ drops relative links) |

## Tests
```
pip install -r requirements-dev.txt && python3 -m pytest -q
```
Scale notes and load-test numbers: NOTES.md.
