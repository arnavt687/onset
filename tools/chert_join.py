#!/usr/bin/env python3
"""Why can't the caregiver be texted? What Chert knows about joining this project's line.

    tools/chert_join.py                          read-only: plan, trial line, join code, number to text
    tools/chert_join.py --typed "join ABC234"    was what the caregiver typed the current phrase?
    tools/chert_join.py --texted-to +15551230000 did they text the right number?
    tools/chert_join.py --show                   print the join phrase and the number in full
    tools/chert_join.py --send-test --yes        send one real test iMessage to the caregiver phone
                                                 (or --to PHONE); the answer says whether it has joined

Issue #29. Chert texts only phones that have joined the project's line, and it has **no way to
list those phones**: not for a project key, not for the owner's sign-in (the CLI's own endpoint
list and some thirty likely addresses were tried on 14 Nov 2026). What the owner's CLI sign-in
(`tools/chert.sh login`) can read is everything that makes a join fail silently:

  * the join phrase now in force and when it last changed (an old code is ignored, no reply);
  * whether joining is switched on;
  * the number the phrase must be texted to;
  * the plan: a trial that has ended, or a trial line that was released (its number may change).

The only test of "has this phone joined" is a send. `recipient_not_verified` means it has not.

Reads use the CLI sign-in and go only to app.trychert.com. Nothing is printed in full unless
--show is given: the phrase and phone numbers are masked, the sign-in token is never shown.
Exit code: 1 if any line is FAIL (or the test send was refused), else 0.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import doctor  # noqa: E402
from tools.doctor import FAIL, PASS, WARN, Check  # noqa: E402

HOST = doctor.CHERT_HOST
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
ROW = "caregiver text"

CHECKLIST = """\
On the caregiver's iPhone, for the join to register:
  1. Settings > Messages > Send & Receive > "Start New Conversations From": the phone NUMBER,
     not an e-mail address. A join that arrives from an Apple ID e-mail does not count.
  2. Send the phrase exactly, as a new one-to-one iMessage (blue bubble) to the number above:
     not a group chat, not SMS (green), not a reply in an old thread with another number.
  3. Wait for Chert's "Verified" reply. It comes in under ten seconds. No reply means the join
     did not register: nothing to wait for, check 1 and 2 and the phrase.
  4. On the free trial the agent may only reply within 24 hours of that phone's last text, so
     have them text the line again shortly before the demo.
Then: tools/chert_join.py --send-test --yes"""


def _get(path: str, token: str, project: str, http: doctor.HttpGet) -> tuple[int, Any]:
    url = f"https://{HOST}{path}"
    if urllib.parse.urlsplit(url).hostname != HOST:  # the sign-in goes nowhere else
        raise RuntimeError("refusing to send the Chert sign-in off app.trychert.com")
    try:
        status, body = http(url, {"Authorization": f"Bearer {token}", "X-Chert-Project-Id": project,
                                  "Accept": "application/json"}, 10.0)
    except OSError as e:
        return 0, {"error": {"code": "network_error", "message": type(e).__name__}}
    return status, doctor._json(body)


def _data(body: Any) -> dict:
    d = body.get("data") if isinstance(body, dict) else None
    return d if isinstance(d, dict) else {}


def _code(body: Any) -> str:
    e = body.get("error") if isinstance(body, dict) else None
    return str(e.get("code") or "") if isinstance(e, dict) else ""


def inspect(env: Mapping[str, str], http: doctor.HttpGet = doctor.urllib_get,
            session: Callable[[Mapping[str, str]], tuple[str, str] | None] = doctor.cli_session) -> dict:
    """The facts, as Chert answers them to the CLI sign-in. Never raises; never holds the token."""
    s = session(env)
    if not s:
        return {"signed_in": False}
    token, project = s
    facts: dict[str, Any] = {"signed_in": True, "errors": {}}

    def read(name: str, path: str) -> dict:
        status, body = _get(path, token, project, http)
        if status != 200:
            facts["errors"][name] = _code(body) or f"http_{status}"
        return _data(body) if status == 200 else {}

    facts["role"] = read("whoami", "/api/v1/cli/whoami").get("role")
    a = read("allowance", "/api/v1/allowance")
    line = a.get("trial_line") if isinstance(a.get("trial_line"), dict) else {}
    facts["plan"] = {"plan": a.get("plan"), "trial_ends_at": a.get("trial_ends_at"), "line_state": line.get("state"),
                     "held_until": line.get("held_until"), "released_at": line.get("released_at")}
    j = read("join", "/api/v1/shared-join-code")
    facts["join"] = {k: j.get(k) for k in ("enabled", "phrase", "receiving_number", "rotated_at")}
    status, body = _get("/api/v1/lines?limit=100", token, project, http)
    d = body.get("data", body) if isinstance(body, dict) else body
    items = d.get("items") if isinstance(d, dict) else d
    facts["lines"] = [{"name": ln.get("display_name"), "phone_number": ln.get("phone_number")}
                      for ln in (items if isinstance(items, list) else []) if isinstance(ln, dict)]
    if status != 200:
        facts["errors"]["lines"] = _code(body) or f"http_{status}"
    return facts


def _when(value: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _ago(t: dt.datetime, now: dt.datetime) -> str:
    s = abs((now - t).total_seconds())
    n, unit = (s / 86400, "day") if s >= 172800 else (s / 3600, "hour") if s >= 5400 else (s / 60, "minute")
    return f"{n:.0f} {unit}{'' if round(n) == 1 else 's'}"


def _digits(phone: Any) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def mask_phrase(phrase: Any) -> str:
    p = str(phrase or "")
    return f"join ••••{p[-2:]}" if len(p) >= 8 else "join ••••••"


def checks(facts: Mapping[str, Any], env: Mapping[str, str], *, typed: str | None = None, texted_to: str | None = None,
           show: bool = False, now: dt.datetime | None = None) -> list[Check]:
    now = now or dt.datetime.now(dt.timezone.utc)
    phone = (lambda p: str(p)) if show else (lambda p: doctor.mask_phone(str(p)))
    out: list[Check] = []
    if not facts.get("signed_in"):
        return [Check(FAIL, "sign-in", "no Chert CLI sign-in on this computer: run `tools/chert.sh login` as the "
                                       "project's owner (a project key cannot read the join code)")]
    errors = facts.get("errors") or {}
    role = facts.get("role")
    if role in ("owner", "admin"):
        out.append(Check(PASS, "sign-in", f"signed in to the Chert CLI as the project's {role}"))
    else:
        out.append(Check(FAIL, "sign-in", f"signed in as {role or 'an unknown role'}"
                                          f"{' (' + errors['whoami'] + ')' if errors.get('whoami') else ''}: only an owner "
                                          "or admin can read the join code. Run `tools/chert.sh login` as the owner"))

    plan = facts.get("plan") or {}
    ends, released = _when(plan.get("trial_ends_at")), _when(plan.get("released_at"))
    if plan.get("plan") == "trial" and ends and ends <= now:
        out.append(Check(FAIL, "plan", f"the free trial ended {_ago(ends, now)} ago: Chert has paused new work for this "
                                       "project, texting included. It needs the Shared plan (`tools/chert.sh plan`)"))
    elif plan.get("plan") == "trial":
        left = f", {_ago(ends, now)} left" if ends else ""
        out.append(Check(WARN, "plan", f"free trial{left}. On the trial the agent may only REPLY, within 24 hours of that "
                                       "phone's last text: have the caregiver text the line again shortly before the demo"))
    elif plan.get("plan"):
        out.append(Check(PASS, "plan", f"{plan['plan']}: the agent may text phones that have joined, at any time"))
    else:
        out.append(Check(WARN, "plan", f"could not read the plan ({errors.get('allowance', 'no answer')})"))
    state = plan.get("line_state")
    if released or state in ("released", "expired", "none"):
        out.append(Check(FAIL, "trial line", f"the trial line was released{' ' + _ago(released, now) + ' ago' if released else ''}"
                                             ". `tools/chert.sh onboard` gets a line again, and its number may be different: "
                                             "anyone who joined must text the phrase to the new number"))
    elif state:
        out.append(Check(PASS, "trial line", f"{state}: the line and its number are still this project's"))

    join = facts.get("join") or {}
    phrase, number = join.get("phrase"), join.get("receiving_number")
    if errors.get("join"):
        hint = " (needs an owner or admin sign-in)" if errors["join"] == "join_code_member_required" else ""
        out.append(Check(FAIL, "join code", f"Chert refused to show the join code: {errors['join']}{hint}"))
    elif join.get("enabled") is not True:
        out.append(Check(FAIL, "join code", "joining is switched OFF for this project: nobody can join, and Chert does not "
                                            "answer a join text. Switch it on at the console's Messages page"))
    else:
        rotated = _when(join.get("rotated_at"))
        out.append(Check(PASS, "join code", f"joining is on. The phrase now in force is \"{phrase if show else mask_phrase(phrase)}\""
                                            f"{', last changed ' + _ago(rotated, now) + ' ago' if rotated else ''}"
                                            f", texted to {phone(number) if number else 'the line (no number given)'}"
                                            f"{'' if show else '  (--show prints both in full)'}"))
        if typed is not None:
            said = re.sub(r"\s+", " ", typed.strip())
            if said.lower() == str(phrase).lower():
                out.append(Check(PASS, "what they typed", "that is the phrase now in force"))
            else:
                odd = sorted(set(re.sub(r"(?i)^join\s*", "", said).upper()) & set("01IO"))
                why = (f" Chert's codes never contain {', '.join(odd)}: a character was misread." if odd else
                       f" The code last changed {_ago(rotated, now)} ago: an older code no longer works." if rotated else "")
                out.append(Check(FAIL, "what they typed", f"\"{said if show else mask_phrase(said)}\" is NOT the phrase now in force "
                                                          f"(\"{phrase if show else mask_phrase(phrase)}\").{why} Chert ignores "
                                                          "a wrong code and sends no reply. Send the current phrase"))
            if not re.fullmatch(r"(?i)join [a-z0-9]{6}", said):
                out.append(Check(WARN, "what they typed", "it does not look like a Chert join phrase: the word join, one "
                                                          "space, then six letters or digits, and nothing else"))
        if number and texted_to is not None:
            same = _digits(texted_to) == _digits(number)
            out.append(Check(PASS if same else FAIL, "number they texted",
                             "that is the number the phrase goes to" if same else
                             f"they texted {phone(texted_to)}, but the phrase must go to {phone(number)}"))
        phones = {_digits(ln.get("phone_number")) for ln in facts.get("lines") or [] if ln.get("phone_number")}
        if number and phones and _digits(number) not in phones:
            out.append(Check(WARN, "number to text", f"the number to text ({phone(number)}) is not the FaceTime number of any "
                                                     "of this project's lines: a join texted to the FaceTime number is lost"))

    cg = (env.get("ONSET_CAREGIVER_PHONE") or "").strip()
    if not cg:
        out.append(Check(WARN, "caregiver phone", "ONSET_CAREGIVER_PHONE is not set (a patients file may set it instead)"))
    elif not _E164.match(cg):
        out.append(Check(FAIL, "caregiver phone", "ONSET_CAREGIVER_PHONE is not an E.164 number such as +15551234567: "
                                                  "Chert matches the sender's number exactly"))
    else:
        out.append(Check(PASS, "caregiver phone", f"{phone(cg)}: it must be the number the join text was sent FROM"))
    out.append(Check(WARN, "who has joined", "Chert does not list the phones that have joined, to anyone. The only test "
                                             "is a send: `tools/chert_join.py --send-test --yes` (recipient_not_verified "
                                             "means that phone has not joined)"))
    return out


def summary(facts: Mapping[str, Any], env: Mapping[str, str], now: dt.datetime | None = None) -> Check:
    """One doctor row from the checks above."""
    rows = [c for c in checks(facts, env, now=now) if c.name not in ("who has joined", "caregiver phone")]
    fails = [c for c in rows if c.status == FAIL]
    if not facts.get("signed_in"):
        return Check(doctor.SKIP, ROW, "needs a Chert CLI sign-in as the owner (`tools/chert.sh login`) to read the join code")
    if fails:
        return Check(FAIL, ROW, "; ".join(f"{c.name}: {c.detail}" for c in fails))
    return Check(WARN, ROW, "joining is on, but Chert cannot say whether the caregiver's phone has joined: run "
                            "`tools/chert_join.py --send-test --yes` before the demo (see docs/chert-messaging.md)")


def load_env(root: Path = ROOT) -> dict[str, str]:
    env = dict(doctor.parse_env_file(root / ".env.local")) if (root / ".env.local").is_file() else {}
    env.update({k: v for k, v in os.environ.items() if v})
    return env


def send_test(env: Mapping[str, str], to: str | None) -> int:
    from onset import chert_api

    target = (to or env.get("ONSET_CAREGIVER_PHONE") or "").strip()
    if not _E164.match(target):
        print("no phone to text: pass --to +15551234567 or set ONSET_CAREGIVER_PHONE")
        return 2
    os.environ.update({k: v for k, v in env.items() if k.startswith("CHERT_") and v})
    res = asyncio.run(chert_api.send_imessage(target, "Onset test: this phone is connected to the check-in line.", dry_run=False))
    line = f"{res.get('status')}"
    if res.get("error"):
        line += f": {res['error']}"
    print(f"send to {doctor.mask_phone(target)} -> {line}")
    if res.get("detail"):
        print(f"  {doctor.scrub(str(res['detail']), env)}")
    if res.get("request_id"):
        print(f"  Chert request id: {res['request_id']}")
    if res.get("accepted"):
        print("  This phone HAS joined: Chert took the message.")
        return 0
    if res.get("error") in ("recipient_not_verified", "recipient_verification_required"):
        print("  This phone has NOT joined the line.\n")
        print(CHECKLIST)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Why can't the caregiver be texted? What Chert knows about joining the line.")
    ap.add_argument("--typed", help='what the caregiver typed, e.g. "join ABC234"')
    ap.add_argument("--texted-to", help="the number they texted it to")
    ap.add_argument("--show", action="store_true", help="print the join phrase and phone numbers in full")
    ap.add_argument("--json", action="store_true", help="print the facts as JSON (masked unless --show)")
    ap.add_argument("--send-test", action="store_true", help="send one real test iMessage (needs --yes)")
    ap.add_argument("--to", help="with --send-test: the phone to text (default ONSET_CAREGIVER_PHONE)")
    ap.add_argument("--yes", action="store_true", help="confirm that --send-test may send a real iMessage")
    args = ap.parse_args(argv)
    env = load_env()
    if args.send_test:
        if not args.yes:
            print("--send-test sends a real iMessage through Chert. Add --yes to send it.")
            return 2
        return send_test(env, args.to)
    facts = inspect(env)
    if args.json:
        safe = json.loads(json.dumps(facts))
        if not args.show and isinstance(safe.get("join"), dict):
            safe["join"]["phrase"] = mask_phrase(safe["join"].get("phrase"))
        text = json.dumps(safe, indent=2)
        print(text if args.show else doctor.mask_phone(text))
        return 0
    rows = checks(facts, env, typed=args.typed, texted_to=args.texted_to, show=args.show)
    width = max(len(c.name) for c in rows)
    for c in rows:
        print(f"{c.status:<4}  {c.name:<{width}}  {c.detail}")
    print()
    print(CHECKLIST)
    return 1 if any(c.status == FAIL for c in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
