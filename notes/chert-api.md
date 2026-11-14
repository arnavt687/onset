# Pieces 6 + 8: chert-api

Closes #7. Closes #9.

Branch `piece/chert-api`, worktree `~/onset-wt/chert-api`. Files changed: `onset/chert_api.py`,
`tests/test_chert_api.py`, `docs/chert-messaging.md`, `docs/outbound.md`, `notes/chert-api.md`.
No new packages (uses `aiohttp`, `python-dotenv` and `livekit-api`, already in the venv).

**Nothing was sent and no call was placed.** Dry runs and a fake transport only; no Chert key
exists yet and none was created; the Chert account was not touched.

## What works

- `send_imessage(recipient, text, dry_run=True)` → `POST /api/v1/shared-messages` with
  `Authorization: Bearer`, `X-Chert-Project-Id`, a fresh `Idempotency-Key` per message; the line
  id from `CHERT_LINE_ID` or `GET /api/v1/lines` (cached). Never raises: returns
  `{status, accepted, error, detail, idempotency_key, message_id, state, request_id}`.
  - `unknown` state → re-read from `GET /api/v1/shared-messages` (3 tries), never re-sent.
  - Lost answer (timeout / 5xx) → one retry with the same key, then `unknown`.
  - 4xx → `error` with Chert's code (sanitised), not retried.
  - The key is sent only to `https://app.trychert.com` (checked per request, no redirects) and
    never appears in results or logs; recipient masked and text not logged.
- `get_message_state(message_id, idempotency_key=)` for later reconcile.
- Piece 8: `mint_bridge_token`, `check_bridge_token`, `place_check_in_call(..., dry_run=True)`
  (`POST /api/v1/calls`, `customer_managed`; 201 allocated, 202 queued, lost answer → same key),
  `get_call` (`bridge_ready` gate).
- `tests/test_chert_api.py`: 40 mocked tests (headers, idempotency, unknown-state, dry run,
  errors, URL guard, bridge token rules, call create/queue/refusal).

## Findings (details in the docs)

- **Messaging** (`docs/chert-messaging.md`): on both plans only phones that *joined* the line
  can be texted. Free trial: the agent only *replies*, within 24 h of the phone's last text.
  A caregiver who never texted the line cannot get the alert on either plan. Options: caregiver
  texts `join <code>` once at enrolment (needed always), Shared plan ($25/mo) for texting any
  time after that, or another channel (SMS/email) as fallback. The 911 instruction must never
  depend on the text.
- **Outbound** (`docs/outbound.md`): the HTTP API documents outbound `customer_managed` calls
  (the "not yet available" note is about the CLI). Shared: documented (verified/joined phones,
  5 min/call, busy-line queue). Trial: not stated either way. Outbound video needs a line
  enabled for it. Base URL in docs.trychert.com is `facetime.trychert.com`; we use only
  `app.trychert.com` as instructed.

## For the main session

- Real send needs: user creates an `ftk_` key at https://app.trychert.com/developer and puts
  `CHERT_API_KEY` + `CHERT_PROJECT_ID` in `~/onset/.env.local`; the caregiver's phone has
  joined the line (on the trial: texted it in the last 24 h); the user's explicit approval.
- Flow should treat anything but `accepted: True` as "tell the person to call the caregiver
  too", and keep `idempotency_key` for an `unknown` result.
- Outbound wiring sketch (room, dispatch, bridge token, writing `chert_bridge_ready` metadata
  for agent.py) is in `docs/outbound.md`.

## Left / next

- Confirm on the first approved real send: the response's state field name and values, and
  the error code for a not-joined recipient.
- Not implemented: busy-queue polling, `POST /calls/{id}/end`, the LiveKit room/dispatch side.
- One approved outbound test answers: trial vs Shared, outbound video, call while `chert dev`
  is connected.
