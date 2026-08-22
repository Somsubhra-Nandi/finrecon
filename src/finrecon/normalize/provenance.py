"""Provenance records for values the normalizer changed.

DESIGN.md's normalization contract (§3 component table) is that the
normalizer owns "integer paise, UTC, canonical schema" and "never does any
matching". A second, equally load-bearing property is auditability: where
normalization changes a value, the original must remain traceable, so an
auditor reading a Stage-2 decision can always reach the source bytes the
decision was ultimately derived from.

Every normalized record therefore carries a :class:`SourceProvenance`
listing each field the normalizer rewrote, with both the source and the
normalized form. Fields the normalizer leaves alone (raw bank narration,
integer-paise amounts) never appear here, which is itself informative: an
empty normalization tuple means nothing was touched.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable, strict, closed-schema base for every Stage-2 model."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class FieldNormalization(FrozenModel):
    """One field the normalizer rewrote, with the source value preserved."""

    field: str
    source_value: str
    normalized_value: str
    rule: str
    """Identifier of the normalization rule applied, e.g. ``"utr.upper_strip"``."""


class SourceProvenance(FrozenModel):
    record_type: str
    record_id: str
    normalizations: tuple[FieldNormalization, ...] = ()

    def source_value_of(self, field: str) -> str | None:
        for entry in self.normalizations:
            if entry.field == field:
                return entry.source_value
        return None
