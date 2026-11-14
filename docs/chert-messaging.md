# Texting a caregiver through Chert

Piece 6 (issue #7). Code: [`onset/chert_api.py`](../onset/chert_api.py), `send_imessage()`.
Sources, read on 2026-11-13: Chert's agent guide <https://app.trychert.com/skills.md>
(sections "HTTP API", "Limits on Shared", "Rules"), the CLI's bundled docs
(`@trychert/facetime@0.1.0-beta.20`: `README.md`, `docs/CLI_ONBOARDING.md`), and
<https://docs.trychert.com>. The section on refusals below was checked against the real API on 14 Nov 2026;
the rest is from the documents. No real message may be sent without the user's approval.

## Bottom line

**On both plans, Chert texts only phones that have joined the project's line.** A caregiver
who has never texted the line cannot get the alert, on the trial or on Shared. On the
**free trial** it is narrower still: the agent can only **reply**, and only **within 24
hours of that phone's last text**. A caregiver who joined three days ago and has been
quiet since cannot be texted on the trial.

So Onset must never depend on the text. The spoken "call 911 now" instruction is the
safety action; the caregiver text is extra, and a failed or impossible text must be told to
the person ("I could not text your caregiver, please call them too").

## When the text is refused: `recipient_not_verified` (issue #29)

Checked against the real API on 14 Nov 2026 (a trial project, an owner's CLI sign-in):

- **`recipient_not_verified` means that phone has not joined this project's line.** It is not a
  code for "the trial may not text first": Chert has separate codes for the plan
  (`shared_entitlement_expired`, `shared_quota_exceeded`, `credit_exhausted`), and its own CLI
  explains this one as "text the code to verify your phone".
- **Chert confirms a join with a "Verified" reply in under ten seconds.** No reply means the join
  did not register; waiting does not help.
- **Nobody can list the phones that have joined.** There is no such endpoint for a project key
  or for the owner's sign-in, and the console does not show it. The only test is a send.
- **The owner's CLI sign-in can read what makes a join fail silently**: the phrase now in force
  and when it last changed (an old code is ignored without a reply), whether joining is on, the
  number the phrase must be texted to, and the plan. A project key is refused
  (`join_code_member_required`).
- **A trial line can be released** (after 3 hours with no agent connected, 15 minutes when lines
  are busy). `chert onboard` gets a line again and **its number may change**: people who joined
  must text the new number.

```bash
tools/chert_join.py                                        # plan, trial line, join code, number to text
tools/chert_join.py --typed "join ABC234" --texted-to +15551230000    # was that the right phrase and number?
tools/chert_join.py --send-test --yes                      # one real test text: the answer says whether the phone has joined
```

Why a join does not register, most likely first:

1. **The text came from an e-mail address.** On the iPhone, Settings > Messages > Send & Receive >
   "Start New Conversations From" must be the phone number. A blue bubble sent from an Apple ID
   e-mail is still iMessage, but Chert sees an e-mail, not the phone.
2. **An old code.** The code was rotated after the QR was shown or copied.
3. **The wrong number**: the line's number changed after a trial release, or the text went into an
   old thread.
4. **Joining is switched off**, or the trial has ended.
5. **The phone already belongs to another project on the same shared line**
   (`shared_recipient_conflict`).

To tell "this phone has not joined" from "this plan cannot text anyone", send the test to the
**owner's own phone** (verified when the account was set up): `tools/chert_join.py --send-test
--yes --to +1...`. If that arrives, the plan can text and the caregiver's phone is the problem.

The text is only a way to deliver the join link. When it is refused, the dashboard still has the
link (the caregiver button), and it can be handed over any other way.

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
