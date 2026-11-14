"""Pieces 6 and 8: Chert's HTTP API for the caregiver iMessage and outbound calls.

Owner: the `chert-api` session. See ROADMAP.md, pieces 6 and 8.
API base: https://app.trychert.com/api/v1 (send keys only to app.trychert.com).
"""

from __future__ import annotations


async def send_imessage(recipient: str, text: str, *, dry_run: bool = True) -> dict:
    """Send `text` to `recipient` over the project's Shared line. Dry run returns the request instead."""
    raise NotImplementedError


async def place_check_in_call(destination: str, *, dry_run: bool = True) -> dict:
    """Ask Chert to FaceTime `destination` and route it to this agent (piece 8, if Chert allows it)."""
    raise NotImplementedError
