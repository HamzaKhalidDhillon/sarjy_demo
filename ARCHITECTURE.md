Sarjy Architecture

Overview

This document explains the high-level architecture, design patterns, and runtime flow used in
the Sarjy demo. It's written for reviewers interested in OOP, reliability, security, and
multistep-workflow trade-offs -- our chosen deep dive is Guardrails & Reliability (see GOALS.md).

High-level flow

1. Frontend (static HTML/JS) records audio and/or accepts typed input, and calls `/stt` or
   `/message`.
2. STT (`/stt` only): audio is streamed to a bounded-size temp file, then run through
   `stt/chain.py`'s provider chain: OpenAI Whisper -> local `faster-whisper` (if enabled) ->
   an offline placeholder that always succeeds.
3. Agent turn (`agent/orchestrator.py`, called by both `/message` and `/stt`):
   a. `agent/guardrails.py`'s `InputGuardrail` checks for jailbreak attempts and prohibited
      topics *before* any LLM or tool call -- a blocked input never reaches a provider.
   b. `agent/memory.py` recalls this user's stored facts (`Memory` rows) and injects them into
      the LLM's context; after the reply, it extracts any new facts from the message.
   c. `agent/state.py`'s `ConversationStateMachine` decides whether this turn is normal chat or
      a step in the meeting-booking flow (`idle -> collecting_time -> awaiting_confirmation ->
      awaiting_contact -> booked`), tracked per-conversation in the `ConversationState` table so
      it survives across turns and can recover if the user goes off-script mid-flow. The
      `awaiting_contact` step exists because Cal.com rejects a booking whose attendee has no
      email or phone -- discovered by testing against the real API, not from its docs.
   d. Normal chat goes to `llm/factory.py`'s runtime-selected provider (`llm/openai_provider.py`,
      `gemini_provider.py`, or `offline_provider.py` -- chosen by the `LLM_PROVIDER` env var, a
      per-request override, or auto-detection by which API key is present).
   e. Booking steps call `tools/calcom/tools.py`'s `CheckAvailabilityTool`/`BookMeetingTool`,
      which wrap `tools/calcom/client.py`'s Cal.com v2 API client. A booking is only ever
      confirmed to the user from `agent/templates.py`, built from a real Cal.com response
      (`data.uid`) -- the LLM is never the source of truth for "it's booked." See "Guardrails
      and reliability" below for how this is enforced structurally.
   f. `agent/guardrails.py`'s `OutputGuardrail` does a final regex pass on every reply, rejecting
      booking-confirmation language unless the orchestrator explicitly marked the turn verified.
4. TTS (`/tts`): reply text goes through `tts/chain.py` (OpenAI TTS -> none); if no server TTS
   is available, the frontend falls back to browser `speechSynthesis`.
5. Persistence: conversations, messages, memory, conversation state, and booking attempts are
   stored via SQLAlchemy models (`models.py`). SQLite for dev; swap to Postgres/Supabase in prod.

Key design patterns

- **Strategy/adapter pattern**: `LLMProvider`, `SttProvider`, `TtsProvider`, and `Tool` are small
  ABCs; each concrete provider/tool is a few lines implementing one method. Adding a provider
  never touches a call site, only the relevant factory/registry (`llm/factory.py`,
  `stt/chain.py`, `tts/chain.py`, `tools/registry.py`).
- **Single Responsibility**: `routers/` is HTTP-only; `agent/` is orchestration/guardrails/state;
  `llm|stt|tts/` are provider integrations; `tools/calcom/` is the one external side-effecting
  integration; `core/` is cross-cutting (config, logging, retry, errors).
- **State machine over free-form reasoning**: the booking flow is an explicit enum
  (`agent/state.py`), not the LLM deciding what step it's on -- this is what makes "recover when
  the user goes off-script" and "never double-book" tractable to reason about and test.
- **Fail-fast + graceful degradation**: STT/LLM/TTS chains degrade to a working (if less capable)
  fallback rather than erroring; an unconfigured Cal.com integration fails with a clear message
  instead of sending a malformed request.

Guardrails and reliability (our deep dive)

- **Input guardrails** run before any provider or tool call and block jailbreak attempts and a
  configurable prohibited-topics list (`agent/guardrails.py::InputGuardrail`).
- **Anti-hallucination on tool results**: a "your meeting is booked" reply is only ever
  constructed in `agent/templates.py::booked()`, called only right after `BookMeetingTool`
  returns a real `uid` from Cal.com. `OutputGuardrail` regex-scans every other reply and rejects
  booking-confirmation language it wasn't told was verified this turn.
  Network-ambiguous outcomes (timeout with no response) are neither claimed booked nor failed --
  the `BookingAttempt` row stays `pending` and the user is told the status is uncertain rather
  than guessed at.
- **Idempotent booking confirmation**: `BookingAttempt.idempotency_key` (hash of conversation +
  event type + requested start) is unique-indexed and written *before* calling Cal.com, so a
  retried or duplicated "yes, book it" can't create two bookings.
- **Retries with backoff**: `core/http.py::request_with_retry()` backs OpenAI, Gemini, and
  Cal.com's read endpoints; Cal.com's booking-creation call is explicitly *not* blindly retried
  (a retried write could double-book) -- ambiguity is resolved via the idempotency flow above.
- **Off-script recovery**: the booking state machine stays parked at its current step (with the
  pending event/slot preserved) across unrelated turns; only an explicit cancel phrase resets it.

Security and deployment notes

- Secrets live in environment variables (`backend/.env.example`), never in source control.
- CORS is now explicit (`main.py`, configurable via `ALLOWED_ORIGINS`) rather than absent.
- Use HTTPS in production and restrict `ALLOWED_ORIGINS` to the real frontend domain.
- `USE_LOCAL_WHISPER=1` allows fully offline transcription for testing without any API key.
