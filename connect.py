"""Checks what a visitor enters to connect a league. Nothing here is ever saved."""
from __future__ import annotations

import re
from typing import Optional

LEAGUE_ID_IN_URL = re.compile(r"leagueId=(\d{1,12})")
SWID = re.compile(r"^\{?([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}?$")
ESPN_S2 = re.compile(r"^[^\s;,\"']{20,2000}$")


class BadInput(ValueError):
    """A message safe to show the visitor; it never repeats what they entered."""


def parse_league_id(text: str) -> int:
    """Accepts a bare league ID or any ESPN league page URL."""
    text = (text or "").strip()
    if text.isdigit() and len(text) <= 12:
        return int(text)
    match = LEAGUE_ID_IN_URL.search(text)
    if match:
        return int(match.group(1))
    raise BadInput("Enter your league ID, or paste the address of your ESPN league page.")


def clean_swid(raw: Optional[str]) -> Optional[str]:
    raw = (raw or "").strip().strip("\"'")
    if not raw:
        return None
    match = SWID.match(raw)
    if not match:
        raise BadInput("That SWID doesn't look right. It's 36 letters, numbers and dashes, usually in {braces}.")
    return "{" + match.group(1).upper() + "}"


def clean_espn_s2(raw: Optional[str]) -> Optional[str]:
    raw = (raw or "").strip().strip("\"'")
    if not raw:
        return None
    if not ESPN_S2.match(raw):
        raise BadInput("That espn_s2 value doesn't look right. Copy the whole value, without spaces.")
    return raw


def clean_cookies(espn_s2: Optional[str], swid: Optional[str]):
    s2, sw = clean_espn_s2(espn_s2), clean_swid(swid)
    if bool(s2) != bool(sw):
        raise BadInput("A private league needs both espn_s2 and SWID.")
    return s2, sw
