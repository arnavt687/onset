# Texting a caregiver through Chert

Piece 6 (issue #7). Code: [`onset/chert_api.py`](../onset/chert_api.py), `send_imessage()`.
Sources, read on 2026-11-13: Chert's agent guide <https://app.trychert.com/skills.md>
(sections "HTTP API", "Limits on Shared", "Rules"), the CLI's bundled docs
(`@trychert/facetime@0.1.0-beta.20`: `README.md`, `docs/CLI_ONBOARDING.md`), and
<https://docs.trychert.com>. Nothing on this page has been tried against the real API yet:
no project API key exists, and no real message may be sent without the user's approval.

## Bottom line

**On both plans, Chert texts only phones that have joined the project's line.** A caregiver
who has never texted the line cannot get the alert, on the trial or on Shared. On the
**free trial** it is narrower still: the agent can only **reply**, and only **within 24
hours of that phone's last text**. A caregiver who joined three days ago and has been
quiet since cannot be texted on the trial.

So Onset must never depend on the text. The spoken "call 911 now" instruction is the
safety action; the caregiver text is extra, and a failed or impossible text must be told to
the person ("I could not text your caregiver, please call them too").

## Who can be texted

| | Free trial (this project, now) | Shared ($25/month) |
| --- | --- | --- |
| Who | Only phones that texted the line, as a **reply within 24 hours** of that phone's last text | Phones that **joined** the project ("may text phones that joined, never a stranger") |
| A caregiver who never texted the line | No | No |
| A caregiver who joined once, weeks ago | No, unless they texted again in the last 24 hours | Yes |
| Phones | 100 | 100 |
| Rate | 50 messages an hour | 100 an hour per project, 400 across the shared line |
| Lasts | 24 hours from activation (48 hours for trials started by 18 Nov 2026) | Monthly |
| Text length | 4,000 characters | 4,000 characters |

Chert's own words (skills.md, "Limits on Shared"), paraphrased: on paid Shared the agent
replies and may text phones that joined, never a stranger; on the free trial the agent only
replies, within 24 hours of a phone's last text. The rules section adds that on a Shared
line you can reach only phones verified for your project.

"Joined" means one of:

- the project owner's phone, verified during `chert onboard` (it texted the 6-digit code);
- anyone who texts **`join <code>`** to the line's number once, over iMessage. The code is
  on the console's Messages page (it can be rotated or joining turned off there), and the
  CLI reads it with `GET /api/v1/shared-join-code`. SMS, group chats and email addresses do
  not count.

Dedicated ($250/month, a line of your own) is not on sale yet; Chert does not say whether
it lifts the joined-phones rule, so do not plan on it.

## What this means for Onset

A caregiver alert is, by nature, the agent texting first, often to someone who has never
heard of the line. That is the case Chert forbids:

1. **Trial (today):** works only if the caregiver texted the line in the last 24 hours. For
   a demo, have the caregiver text `join <code>` (or anything) to the line shortly before
   the call. Outside a demo this is not usable.
2. **Shared:** works once the caregiver has joined, with no time window. Joining is a
   one-time step at enrolment.

Even when allowed, a `queued` or `sent` result means Chert took the message, not that the
caregiver read it.

## Options

1. **Caregiver texts "join &lt;code&gt;" once, at enrolment** (needed on every plan). Do it when
   the person's baseline is recorded: the caregiver texts the phrase from their own iPhone,
   Chert confirms, and the enrolment records their number. On the trial, this only buys 24
   hours.
2. **Upgrade to Shared** ($25/month, owner pays in the browser at
   <https://app.trychert.com/billing>; `chert plan shared` opens it). Combined with option 1
   this is the only Chert-only way to text a caregiver at any time. Limits: 100 phones,
   100 messages an hour.
3. **Another channel as a fallback or instead**: SMS (for example Twilio, which needs its
   own opt-in and A2P registration), email, or a phone call to the caregiver. Not built here;
   it would be a new piece. Whatever the channel, keep the "never the only safety action" rule.

Recommendation: option 1 + 2 for anything beyond a demo, and for the demo on the trial, have
the caregiver text the line just before the call. Ask the user before choosing a paid plan.

## How `send_imessage` behaves

```python
from onset import chert_api
r = await chert_api.send_imessage("+15551234567", summary)                 # dry run (default)
r = await chert_api.send_imessage("+15551234567", summary, dry_run=False)  # real: needs approval
```

- **Config:** `CHERT_API_KEY` (an `ftk_` project key from <https://app.trychert.com/developer>,
  owners only, with `facetime:write`), `CHERT_PROJECT_ID`, optional `CHERT_LINE_ID`; read
  from the environment or `.env.local`, never printed. Without `CHERT_LINE_ID` it asks
  `GET /api/v1/lines` once and uses the only line (several lines: set the variable).
- **Request:** `POST https://app.trychert.com/api/v1/shared-messages` with
  `Authorization: Bearer`, `X-Chert-Project-Id`, a fresh `Idempotency-Key`
  (`onset-msg-<uuid4>`) and `{"line_id", "recipient", "text"}`.
- **Key safety:** URLs are built from one constant and checked to be
  `https://app.trychert.com`; redirects are not followed; results and logs never contain
  the key; the dry run shows `Bearer ***`. The recipient is masked in logs and the text is
  not logged.
- **Never raises.** It returns a dict: `status`, `accepted`, `error`, `detail`,
  `idempotency_key`, and when known `message_id`, `state`, `request_id`.

| `status` | Meaning | What the flow should do |
| --- | --- | --- |
| `dry_run` | Nothing sent; `request` holds what would be sent | Nothing (tests, demos) |
| `queued` / `sent` / `delivered` | Chert took it (`accepted: True`) | Say the caregiver was texted (not that they read it) |
| `unknown` | Chert kept it but has not confirmed, or the answer was lost | Tell the person to call the caregiver too; later call `get_message_state()` or retry with the **same** `idempotency_key`. Never resend with a new key |
| `failed` | Chert says the send failed | Tell the person to call the caregiver |
| `error` | Refused: bad input, no key, not joined, rate limit, ... (`error` = code) | Same as failed; log the code |

Retries: a lost answer (timeout or HTTP 5xx) is retried once with the same key, then
reported as `unknown`. A `4xx` is never retried. An `unknown` state is re-read up to three
times from `GET /api/v1/shared-messages` (the newest 50), matching on the message id.

Assumptions to confirm on the first approved real send (the docs do not show them): the
response field holding the send state (the code reads `state`, then `send_state`,
`delivery_state`, `status`), the exact state names, and the error code for a recipient who
has not joined (passed through as Chert's own code).

The base URL: skills.md and the CLI use `https://app.trychert.com/api/v1`; some pages of
docs.trychert.com use `https://facetime.trychert.com/api`. Onset sends its key only to
`app.trychert.com`, as instructed; switching host needs the user's decision.
