Sarjy Demo (Monorepo)

Voice assistant demo for the Sarj take-home (see `instruction.md` for the brief, `GOALS.md` for
our specific decisions and write-up notes).

Services:
- `backend` — FastAPI (Python), modular by provider/agent/tool (see "Backend structure" below),
  with SQLite persistence, runtime-selectable LLM providers, cross-session memory, deterministic
  guardrails, and a Cal.com-backed meeting-booking agent.
- `frontend` — a single static HTML/JS page (intentionally simple; this project's engineering
  effort went into the backend, not the UI) that talks to the backend.

Quick start (local development):

1. Copy `backend/.env.example` to `backend/.env` and edit any keys you want (all optional except
   Cal.com, if you want the booking flow to work end to end -- see GOALS.md).

2. Start services with Docker Compose:

```bash
docker-compose up --build
```

3. Frontend: http://localhost:8000/frontend/index.html
   Backend: http://localhost:8000

Or run locally without Docker, from the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Notes:
- The backend uses SQLite (`./data/data.db`, relative to wherever the process is started -- the repo root both locally and in Docker) by default.
- With no `OPENAI_API_KEY`/`GEMINI_API_KEY` set, the assistant runs fully offline (deterministic
  echo replies) -- useful for exercising the memory/guardrail/booking-state-machine plumbing
  without any provider cost.
- For demos you can run the frontend anywhere static files are served and the backend on any
  host that can run a Python container. SQLite is recommended for local/dev and Postgres
  (Supabase/Neon/etc.) for production.

Backend structure
- `core/` -- config (env-driven `Settings`), structured logging, retry/backoff HTTP helper, errors.
- `llm/`, `stt/`, `tts/` -- one provider class per backend (OpenAI/Gemini/offline, etc.) behind a
  common interface, selected at runtime by `llm/factory.py` (env var `LLM_PROVIDER`, or a
  per-request override in the `/message` body) -- this is the "common file that decides which
  model we use" the project structure was reorganized around.
- `tools/calcom/` -- a thin Cal.com v2 API client plus `Tool` wrappers (check availability, book,
  cancel), shaped like an MCP tool (name/description/JSON-schema params/`run()`) so it could be
  fronted by a real MCP server later without changing the calling convention.
- `agent/` -- `orchestrator.py` runs one conversation turn: guardrails first, then memory
  recall/extraction, then either normal chat or the booking state machine
  (`idle -> collecting_time -> awaiting_confirmation -> booked`). `templates.py` is the only place
  booking-outcome text is composed, always from real Cal.com response data.
- `routers/` -- thin FastAPI routers (same endpoint paths/shapes as before) that call into the
  above.

Local Whisper (optional)
- `faster-whisper` is kept out of the base `backend/requirements.txt` -- it pulls in `av`, which
  builds from source and needs system `libav*` dev headers (e.g. `brew install ffmpeg` on macOS),
  which isn't always available out of the box. Install it separately if you want local
  transcription: `pip install -r backend/requirements-whisper.txt`.
- Then set `USE_LOCAL_WHISPER=1` and optionally `LOCAL_WHISPER_MODEL_SIZE` (e.g., `small`,
  `medium`, `large`) and `LOCAL_WHISPER_DEVICE` (`cpu` or `cuda`).
- Note: `faster-whisper` downloads model weights the first time and requires disk space and (for larger models) a GPU.
- Add to `.env` if you want local transcription:

```
USE_LOCAL_WHISPER=1
LOCAL_WHISPER_MODEL_SIZE=small
LOCAL_WHISPER_DEVICE=cpu
```

The `/stt` endpoint will try OpenAI Whisper first if `OPENAI_API_KEY` is set; otherwise it will fall back to local `faster-whisper` if enabled, and finally to an offline placeholder.

Health check and tests
- Check local Whisper health: `GET /whisper_health` (returns `local_whisper_available`).
- Test STT endpoint with a local audio file:

```bash
python tests/stt_test.py path/to/sample.webm
```

Local run without Docker (recommended for testing local Whisper):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Google Gemini (AI Studio) quick test
- Paste your Gemini API key into `backend/.env.example` (or your `.env`) as `GEMINI_API_KEY`.
- Start the backend and call the test endpoint:

```bash
curl -X POST "http://localhost:8000/gemini_test" -d "prompt=Hello from test"
```

The endpoint will attempt a small generation using the Generative Language REST API (`text-bison-001`) with the API key supplied as `?key=` and return the provider response. If your account uses a different Gemini/AI Studio endpoint or authentication method, skip this test and provide the correct endpoint or service account flow.

**Audience & Design Notes**

This project is prepared for reviewers who care about software engineering discipline (OOP, abstraction, maintainability) and system-level concerns (reliability, security, bandwidth). The notes below explain design choices you should be able to justify in a deep technical discussion.

- **Audience:** Engineers and reviewers focused on architecture, OOP, and production-readiness (reliability, security, performance, cost).

- **OOP & Abstraction:**
   - **Clear adapters:** Provider-specific logic (OpenAI, Gemini, local Whisper, Cal.com) is isolated behind small provider/tool classes in `llm/`, `stt/`, `tts/`, `tools/calcom/`, each implementing a common interface, so callers depend on stable contracts, not provider details.
   - **Single Responsibility:** `core/` (config/logging/http), `llm|stt|tts/` (providers), `tools/` (external actions), `agent/` (orchestration/guardrails/state), `routers/` (HTTP) each own one concern.
   - **Extensible interfaces:** Adding a provider means one new class implementing `LLMProvider`/`SttProvider`/`TtsProvider`/`Tool`, registered in the relevant factory/registry -- no call sites change.

- **Reliability & Observability:**
   - **Fallback chains:** STT/LLM/TTS follow a deterministic fallback order (OpenAI → local whisper → offline placeholder) to reduce single-provider outages during demos.
   - **Durable persistence:** Conversations, messages, memory, and booking attempts are persisted (SQLite for dev; swap to Postgres/Supabase for production).
   - **Idempotency & retries:** `core/http.py`'s `request_with_retry()` backs every outbound call (OpenAI/Gemini/Cal.com) with backoff on 429/5xx; booking confirmations are additionally guarded by a DB-level idempotency key (`BookingAttempt.idempotency_key`) so a retried or duplicated "yes, book it" can't create two Cal.com bookings.
   - **No hallucinated tool results:** the agent never tells the user a meeting is booked unless Cal.com's API actually returned a booking id that turn -- see `agent/templates.py` and `agent/guardrails.py`'s output check.
   - **Logging:** structured, per-request-id logging with per-pipeline-stage timing (`core/logging.py`) for post-demo latency analysis; Prometheus/Grafana integration is a natural next step, not done here.

- **Security & Least Privilege:**
   - **Secrets in env:** API keys and credentials live in environment variables (see `backend/.env.example`) and must never be checked into source control.
   - **HTTPS & CORS:** Serve backend via HTTPS in production and apply strict CORS rules to the frontend host only.
   - **Input validation & sanitization:** All user inputs (audio uploads, text fields) should be size-limited, scanned for abuse, and validated server-side before processing.
   - **Provider credentials:** Use provider-recommended auth (service accounts for Gemini/Vertex, scoped API keys for OpenAI) and rotate keys regularly.

- **Bandwidth & Cost Optimizations:**
   - **Compressed audio:** Record and upload compressed audio (webm/opus) to reduce upload size and latency. The frontend uses `audio/webm` by default.
   - **Server-side caching & TTLs:** Cache repeated LLM/TTS responses where appropriate to avoid redundant provider calls for identical inputs.
   - **Chunked/streaming:** For long audio or long model outputs, support streaming/transcription chunks instead of buffering full payloads in memory.
   - **Model selection:** Prefer smaller, cheaper models for demo flows; allow environment-driven model selection for easier cost control.

- **Testing & Verification:**
   - Unit tests for the LLM provider factory, guardrails, Cal.com tools (mocked client, no network), and the booking state machine (in-memory SQLite). Integration test for `/stt` (see `tests/test_stt_integration.py`).
   - Not yet done: security fuzz testing and load testing -- see `GOALS.md`'s future-work list.

- **Demo Talking Points:** Be prepared to explain:
   - Why the provider/tool interfaces make swapping models or adding tools cheap.
   - How the booking flow structurally prevents a hallucinated confirmation (not just a prompt instruction) -- see `agent/orchestrator.py` and `agent/guardrails.py`.
   - How you'd productionize persistence (Alembic migrations, Postgres, backups) and secrets (vaults, IAM) -- see `GOALS.md`.

Running tests

```bash
pip install -r backend/requirements.txt pytest pytest-asyncio
pytest -q tests/                       # everything except the STT integration test needs no server/keys
uvicorn backend.main:app --reload --port 8000 &
pytest -q tests/test_stt_integration.py   # needs the server running; self-skips without ffmpeg
```


Local Whisper dev setup

To run and test transcription locally (recommended for offline development and demonstrations):

1. Create and activate a Python virtualenv in the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-whisper.txt
```

2. Install system `ffmpeg` (macOS Homebrew):

```bash
brew install ffmpeg
```

3. Enable local whisper in `backend/.env`:

```
USE_LOCAL_WHISPER=1
LOCAL_WHISPER_MODEL_SIZE=small
LOCAL_WHISPER_DEVICE=cpu
```

4. Start the backend locally and test the `/whisper_health` endpoint:

```bash
uvicorn backend.main:app --reload --port 8000
curl http://localhost:8000/whisper_health
```

Notes:
- The first run of `faster-whisper` will download model weights which can be tens or hundreds of megabytes depending on model size.
- For faster local transcription, use a machine with a CUDA-capable GPU and set `LOCAL_WHISPER_DEVICE=cuda`.




