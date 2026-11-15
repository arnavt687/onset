"""Patient profiles: who is calling, whose baseline to use, and who their caregiver is.

A line serves the people its care team prescribed Onset to. Each person has a profile:

    config/patients.json   (gitignored: it holds phone numbers; see config/patients.example.json)

    {
      "default": "pat-001",                 # optional: used when the caller cannot be matched
      "patients": [
        {
          "id": "pat-001",                  # baseline file name (letters, digits, _ + - .)
          "name": "Alex",                   # used in the caregiver's summary
          "phones": ["+15551230001"],       # the caller's FaceTime phone(s) or Apple ID email(s)
          "caregiver": {"name": "Sam", "phone": "+15551230002"},
          "sentence": "optional: a fixed sentence for this person's first check-in"
        }
      ]
    }

`ONSET_PATIENTS_FILE` points somewhere else. With no file, one profile is built from the
environment (`ONSET_PATIENT_ID`, `ONSET_PATIENT_NAME`, `ONSET_CAREGIVER_PHONE`,
`ONSET_CAREGIVER_NAME`), which is how a single-person demo line runs.

Matching: phones are compared as digits only (so "+1 (555) 123-0001" matches "+15551230001");
emails case-insensitively. Chert may report the caller only partly (masked) or not at all; then
the `default` profile is used, and with no default and several profiles the result is the
anonymous profile `unknown`, whose check-in is never saved as anyone's baseline.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO / "config" / "patients.json"

_ID_RE = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9_+.-]{0,127}$")  # same rule as onset.decide
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

UNKNOWN_ID = "unknown"


class ProfileError(ValueError):
    """The profiles file is unreadable or invalid (message names the problem, never a phone)."""


@dataclass(frozen=True)
class Caregiver:
    name: str = ""
    phone: str = ""  # E.164, or "" when none is set

    def __repr__(self) -> str:  # keep phone numbers out of logs
        return f"Caregiver(name={self.name!r}, phone={mask(self.phone)!r})"


@dataclass(frozen=True)
class Patient:
    id: str
    name: str = ""
    phones: tuple[str, ...] = ()
    caregiver: Caregiver = field(default_factory=Caregiver)
    sentence: str | None = None
    matched_by: str = "default"  # "phone" | "default" | "env" | "unknown"

    @property
    def anonymous(self) -> bool:
        return self.id == UNKNOWN_ID

    def __repr__(self) -> str:
        return (f"Patient(id={self.id!r}, name={self.name!r}, phones={[mask(p) for p in self.phones]!r}, "
                f"caregiver={self.caregiver!r}, matched_by={self.matched_by!r})")


@dataclass(frozen=True)
class Profiles:
    patients: tuple[Patient, ...]
    default: str | None = None
    source: str = "env"  # the file path, or "env"

    def by_id(self, patient_id: str) -> Patient | None:
        return next((p for p in self.patients if p.id == patient_id), None)

    def lookup(self, caller: str | None) -> Patient:
        """The profile for a caller handle (phone or email, possibly None)."""
        key = normalize(caller)
        if key:
            for p in self.patients:
                if key in {normalize(x) for x in p.phones}:
                    return _with(p, "phone")
        if self.default:
            p = self.by_id(self.default)
            if p is not None:
                return _with(p, "env" if self.source == "env" else "default")
        if len(self.patients) == 1:
            return _with(self.patients[0], "default" if self.source != "env" else "env")
        return Patient(id=UNKNOWN_ID, name="", matched_by="unknown")


def _with(p: Patient, how: str) -> Patient:
    return Patient(p.id, p.name, p.phones, p.caregiver, p.sentence, how)


def handle_from_metadata(meta: Mapping[str, Any] | None) -> str | None:
    """The caller's phone or Apple ID from a call's job metadata, when Chert provides it.

    `chert dev` does not today (its dispatch metadata is {"chert_call", "media"}; only BYOR
    connectors receive `from`), so this is usually None and the line's default profile is used.
    """
    if not isinstance(meta, Mapping):
        return None
    for key in ("from", "caller", "from_handle", "remote_handle"):
        v = meta.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def mask(handle: str | None) -> str:
    """'+15551230001' -> '+1…01'. For logs and events; never log a full number."""
    if not handle:
        return ""
    if "@" in handle:
        user, _, dom = handle.partition("@")
        return f"{user[:1]}…@{dom}"
    return handle[:2] + "…" + handle[-2:] if len(handle) > 6 else "…"


def normalize(handle: str | None) -> str:
    if not handle or not isinstance(handle, str):
        return ""
    h = handle.strip()
    if "@" in h:
        return h.lower()
    digits = re.sub(r"\D", "", h)
    if len(digits) == 10:  # a US number written without the country code
        digits = "1" + digits
    return digits if len(digits) >= 7 else ""


def _check_handle(h: Any, where: str) -> str:
    if not isinstance(h, str) or not (_E164.match(h.strip()) or _EMAIL.match(h.strip())):
        raise ProfileError(f"{where}: must be an E.164 phone (+15551234567) or an email")
    return h.strip()


def _parse(data: Any, source: str) -> Profiles:
    if not isinstance(data, dict) or not isinstance(data.get("patients"), list):
        raise ProfileError(f"{source}: expected an object with a 'patients' list")
    out: list[Patient] = []
    seen_ids: set[str] = set()
    seen_phones: dict[str, str] = {}
    for i, raw in enumerate(data["patients"]):
        where = f"{source}: patients[{i}]"
        if not isinstance(raw, dict):
            raise ProfileError(f"{where}: expected an object")
        pid = raw.get("id")
        if not isinstance(pid, str) or not _ID_RE.fullmatch(pid) or pid == UNKNOWN_ID:
            raise ProfileError(f"{where}.id: letters, digits, _ + - . only (and not '{UNKNOWN_ID}')")
        if pid in seen_ids:
            raise ProfileError(f"{where}.id: duplicate id {pid!r}")
        seen_ids.add(pid)
        name = raw.get("name", "")
        if not isinstance(name, str):
            raise ProfileError(f"{where}.name: expected text")
        phones_raw = raw.get("phones", [])
        if isinstance(phones_raw, str):
            phones_raw = [phones_raw]
        if not isinstance(phones_raw, list):
            raise ProfileError(f"{where}.phones: expected a list")
        phones = tuple(_check_handle(h, f"{where}.phones[{j}]") for j, h in enumerate(phones_raw))
        for h in phones:
            n = normalize(h)
            if n in seen_phones and seen_phones[n] != pid:
                raise ProfileError(f"{where}.phones: {mask(h)} also belongs to {seen_phones[n]!r}")
            seen_phones[n] = pid
        cg_raw = raw.get("caregiver") or {}
        if not isinstance(cg_raw, dict):
            raise ProfileError(f"{where}.caregiver: expected an object")
        cg_phone = cg_raw.get("phone", "") or ""
        if cg_phone:
            cg_phone = _check_handle(cg_phone, f"{where}.caregiver.phone")
            if not _E164.match(cg_phone):
                raise ProfileError(f"{where}.caregiver.phone: must be an E.164 phone")
        cg_name = cg_raw.get("name", "") or ""
        if not isinstance(cg_name, str):
            raise ProfileError(f"{where}.caregiver.name: expected text")
        sentence = raw.get("sentence")
        if sentence is not None and (not isinstance(sentence, str) or not sentence.strip()):
            raise ProfileError(f"{where}.sentence: expected non-empty text")
        out.append(Patient(pid, name, phones, Caregiver(cg_name, cg_phone), sentence, "default"))
    default = data.get("default")
    if default is not None and default not in seen_ids:
        raise ProfileError(f"{source}: default {default!r} is not one of the patients' ids")
    if not out:
        raise ProfileError(f"{source}: no patients")
    return Profiles(tuple(out), default, source)


def from_env(env: Mapping[str, str] | None = None) -> Profiles:
    env = os.environ if env is None else env
    pid = env.get("ONSET_PATIENT_ID", "demo") or "demo"
    if not _ID_RE.fullmatch(pid):
        raise ProfileError("ONSET_PATIENT_ID: letters, digits, _ + - . only")
    phone = (env.get("ONSET_CAREGIVER_PHONE") or "").strip()
    if phone and not _E164.match(phone):
        raise ProfileError("ONSET_CAREGIVER_PHONE: must be an E.164 phone (+15551234567)")
    own = (env.get("ONSET_PATIENT_PHONE") or "").strip()  # where Onset calls the person back (group call)
    if own and not _E164.match(own):
        raise ProfileError("ONSET_PATIENT_PHONE: must be an E.164 phone (+15551234567)")
    p = Patient(pid, env.get("ONSET_PATIENT_NAME", "") or "Demo patient", (own,) if own else (),
                Caregiver(env.get("ONSET_CAREGIVER_NAME", "") or "", phone), None, "env")
    return Profiles((p,), pid, "env")


def profiles_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    p = env.get("ONSET_PATIENTS_FILE")
    return Path(p).expanduser() if p else DEFAULT_FILE


def load_profiles(path: Path | None = None, env: Mapping[str, str] | None = None) -> Profiles:
    """Profiles from the file (if it exists), else from the environment. Raises ProfileError."""
    path = path if path is not None else profiles_path(env)
    if not path.is_file():
        if path != DEFAULT_FILE and (env if env is not None else os.environ).get("ONSET_PATIENTS_FILE"):
            raise ProfileError(f"ONSET_PATIENTS_FILE points to a missing file: {path}")
        return from_env(env)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError(f"{path.name}: cannot read ({type(e).__name__})") from None
    return _parse(data, path.name)


def lookup(caller: str | None, *, path: Path | None = None, env: Mapping[str, str] | None = None) -> Patient:
    """The caller's profile. Never raises: a broken file falls back to the environment profile."""
    try:
        return load_profiles(path, env).lookup(caller)
    except ProfileError:
        import logging

        logging.getLogger("onset.patients").exception("[onset] patient profiles are invalid; using the env profile")
        try:
            return from_env(env).lookup(caller)
        except ProfileError:
            return Patient(id=UNKNOWN_ID, matched_by="unknown")
