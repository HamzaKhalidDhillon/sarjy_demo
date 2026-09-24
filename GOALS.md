# Sarjy: Goals & Decisions

This is the working plan for the Sarj take-home (`instruction.md`), kept up to date as we build.
It's meant to double as the seed for the write-up the brief asks for.

## What the assignment requires

Build, deploy, and present a voice assistant ("Sarjy") that: listens/responds by voice, remembers
facts/preferences across sessions, calls at least one external API that adds real value
(justified in 2-3 sentences), and is deployed to a URL a reviewer can open with no special setup.
Additionally: pick **one** "deep dive" area and go deep on it -- "one done well is better than
three done shallowly."

## Decisions

### Deep dive: Guardrails & Reliability

We're prioritizing reliability and security together, since a scheduling agent's actual failure
modes -- a hallucinated confirmation, a double-booked slot, a jailbreak attempt, losing track of
a booking when the user goes off-script -- are exactly what "guardrails and reliability" means
for this kind of assistant. Concretely, we built:

- Deterministic input guardrails (jailbreak/prohibited-topic detection) that run before any LLM
  or tool call.
- A structural (not just prompted) guarantee that the assistant never claims a meeting is booked
  unless Cal.com's API actually returned a real booking id that turn -- see
  `agent/templates.py` and `agent/guardrails.py::OutputGuardrail`.
- An idempotency key on booking confirmations so a retried "yes, book it" can't double-book.
- A retry/backoff helper shared by every external call (OpenAI, Gemini, Cal.com), with booking
  *creation* specifically excluded from blind retries (a retried write could double-book) --
  ambiguous outcomes are reconciled instead of guessed at.

Latency got real attention too (structured per-stage timing in every pipeline run -- see
`core/logging.py`) but isn't the headline; we didn't do a dedicated time-to-first-audio
optimization pass.

### The use case, concretely

Sarjy is a voice-based scheduling receptionist -- a voice front-end for a Cal.com booking page.
Someone talks to it, says they want to set up a quick intro chat with the team, and Sarjy checks
real availability and books directly onto one fixed calendar. No per-caller calendars, no OAuth,
no picking between multiple hosts -- deliberately narrow and demoable rather than a general-
purpose "do anything" assistant. This is also a real, existing category (AI voice receptionists
that replace "click this Calendly link"), which makes it a believable answer to "why this API,
why this use case" rather than a contrived one.

### External API: Cal.com (meeting scheduling)

We chose Cal.com over a weather/maps API because it lets Sarjy do something genuinely useful for
a voice assistant in the Sarj (voice AI) space: check real availability and book a real meeting
end to end, not just fetch a read-only fact. It's a real external API with a side effect (an
actual scheduled meeting, confirmation email, video link), which makes the "don't hallucinate
when using tools" reliability story concrete instead of hypothetical. Auth is a single free API
key (no OAuth), which kept setup cost low relative to Google Calendar.

**Setup required to actually exercise the booking flow:**
1. Create a free account at cal.com and generate an API key (Settings -> Security).
2. Create one Event Type (e.g. a 30-minute meeting) and note its numeric event type id and your
   Cal.com username.
3. In `backend/.env`, set `CALCOM_API_KEY`, `CALCOM_USERNAME`, `CALCOM_EVENT_TYPE_ID`.
4. Cal.com versions its API per-endpoint via a `cal-api-version` header. The defaults in
   `.env.example` (`CALCOM_API_VERSION_SLOTS`, `CALCOM_API_VERSION_BOOKINGS`) were pulled from
   cal.com/docs at implementation time -- if slot lookups or bookings start failing, check
   `https://cal.com/docs/api-reference/v2/{slots,bookings}` for the current value and override it
   via `.env` (no code change needed).

Without a Cal.com key configured, the booking flow fails gracefully with a clear "can't reach the
calendar right now" message rather than crashing -- see `CalComClient._ensure_configured()`.

**Verified end to end against a real account**, not just in unit tests: availability check ->
slot selection -> confirmation -> email collection -> real `POST /v2/bookings` -> real booking
(with a Cal Video link and a Cal.com-side confirmation) -> cancelled via `cancel_booking` for
cleanup. Two things only live testing caught, now fixed:
- `GET /v2/event-types` 404s if a `cal-api-version` header is sent at all (unlike slots/bookings,
  which require one) and its response shape is `data.eventTypeGroups[*].eventTypes[*]`, not a
  flat list -- `tools/calcom/client.py` was fixed to match.
- Cal.com rejects a booking whose attendee has no email or phone ("Attendee must have at least
  one contact method"), even though the docs summary we started from said name+timezone were
  sufficient. The booking flow now has an explicit `awaiting_contact` state that asks for an
  email before ever calling `create_booking` -- see `agent/state.py`'s `BookingState` and
  `agent/orchestrator.py::_collect_contact`.

### Memory: cross-session recall

`agent/memory.py` recalls all of a user's stored `Memory` facts before every LLM call and extracts
new ones (favorite X, name, location, employer, explicit "remember that..." statements) after.
This is deliberately a fast deterministic regex extractor, not a second LLM call -- cheap,
testable, no added latency. This is what makes "what's my favorite color?" actually work across
sessions; previously `/memory/set`/`/memory/get` existed but nothing called them automatically.

### Frontend: intentionally minimal

Effort went into the backend per the deep-dive choice above. The frontend stays the simple static
page it already was.

## Architecture

See `ARCHITECTURE.md` for the detailed flow and design patterns. Summary: modular
provider/tool/agent structure under `backend/`, with `llm/factory.py` as the "pick a model at
runtime" file the project was reorganized around, `tools/calcom/` as the one external
side-effecting tool (shaped MCP-tool-compatible for an easy future swap), and
`agent/orchestrator.py` running guardrails -> memory -> (chat | booking state machine) -> output
guardrail for every turn.

## Future work (explicitly out of scope for this pass)

- **Real standalone MCP server process** -- `tools/base.py`'s `Tool` shape (name, description,
  JSON-schema params, `run()`) is intentionally MCP-tool-compatible, but we didn't stand up an
  actual MCP server/client transport for this deadline.
- **Full circuit breakers** with cross-request provider health tracking -- the per-request
  fallback chains are reliability enough for a take-home.
- **OAuth for Cal.com** -- explicitly avoided; API key + one event type was the right amount of
  setup for this scope.
- **Multi-tenant auth** -- single demo `user_id` string, no login/session security model.
- **Alembic migrations** -- `Base.metadata.create_all()` is fine for two new tables at this scale.
- **LLM-based moderation as the primary guardrail** -- deterministic regex/keyword checks are
  the primary layer; an LLM classifier behind a flag would be a reasonable second pass.
- **Booking reconciliation via a real Cal.com `GET /bookings` lookup** -- ambiguous
  (network-timeout) booking attempts currently stay `pending` and ask the user to retry rather
  than being automatically reconciled against Cal.com's own record.
- **Actual cloud deployment** -- needs hosting credentials only the account owner has; a
  reasonable free-tier path is a single Render/Fly.io web service for the backend (which also
  serves the static frontend at `/frontend`), pointed at a Postgres addon instead of SQLite for
  anything beyond a demo.
- **Security note**: `backend/.env.example` previously had a real Gemini API key checked in by
  mistake -- it's been blanked, but that key should be rotated in Google AI Studio since it was
  exposed.
