"""Small access policy boundary for confidential S&D data."""
from __future__ import annotations


def may_access_real_snd(access_mode: str | None) -> bool:
    """Real requirements/evidence are available only after member login."""
    return access_mode == "member"
