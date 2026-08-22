"""Shared base configuration for canonical financial record models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CanonicalRecord(BaseModel):
    """Base for every canonical financial record type.

    ``strict=True`` stops pydantic's default lax coercion (e.g. numeric
    strings quietly becoming ints) from opening a side door around the
    explicit ``Paise`` boundary conversions in :mod:`finrecon.models.money`.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
