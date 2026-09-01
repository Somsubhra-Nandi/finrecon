"""Read-only recognition of an already-reviewed bank schema.

This is *detection*, and it is worth being pedantic about what that does
and does not mean, because three different things are easy to confuse:

1. **Detection** -- "the header row in this file is the header row profile
   ``X`` declares". That is all this module does.
2. **Inference** -- "these columns probably mean debit and credit". Not
   here. A profile's debit column, credit column, value-date format,
   narration, reference and inactive-side semantics are only ever read off
   a human-reviewed registry artifact, never derived from a file.
3. **Authorization** -- "so go ahead and reconcile". Also not here: this
   module returns a finding, and the caller decides.

Nothing in this path builds a ``BankRecord``, opens a batch, touches the
ledger, or reaches a model. There is no prompt, no provider, no confidence
score and no ranking -- a schema either equals a reviewed one or it does
not.

**Fail closed.** Two profiles matching at the same strongest tier is
:attr:`MatchStatus.AMBIGUOUS`, and no tie-break exists to resolve it: not
newest version, not highest version, not alphabetical, not registration
order. No profile matching is :attr:`MatchStatus.UNKNOWN`, and the nearest
profile is deliberately not offered as a fallback. Both outcomes route the
user to the manual-profile path, which is the only thing that can safely
resolve them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .registry import BankProfileRegistry, BuiltInProfile, ProfileSelectionMode
from .signature import BankSchemaReadError, SchemaSignature, read_signature

DISPLAY_ENCODING = "utf-8-sig"
"""Encoding used only for the header row FinRecon *reports* back.

BOM-tolerant so a Windows-authored export does not display a mangled first
column. This read never decides a match: a candidate profile is always
compared under *its own* declared encoding and delimiter (see
:func:`inspect_bank_csv`), so recognition never depends on a guess made
here.
"""

DISPLAY_DELIMITER = ","
"""Delimiter used only for the reported header row, for the same reason."""


class MatchStatus(str, Enum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class MatchTier(str, Enum):
    """The two tiers strong enough to select a profile automatically.

    Nothing weaker exists on purpose. Punctuation stripping, abbreviation
    and synonym expansion, fuzzy/edit-distance similarity, subset or
    superset matching, and reordered columns are all absent -- each is a
    guess about meaning, and belongs to a future proposal layer where a
    human confirms the mapping before it is used.
    """

    EXACT = "exact"
    """The header row is byte-for-byte the profile's declared header row,
    in order, read under the profile's declared delimiter and encoding."""

    SAFE_NORMALIZED = "safe_normalized"
    """Identical after representation-only fixes -- a UTF-8 BOM on the
    first header, leading/trailing whitespace, repeated whitespace, and
    letter case. Nothing about column identity is being inferred."""


@dataclass(frozen=True)
class BankSchemaInspection:
    """What a read-only inspection of one uploaded statement found."""

    status: MatchStatus
    observed: SchemaSignature
    """The header row as FinRecon read it for display (see
    :data:`DISPLAY_ENCODING`) -- kept verbatim so a reviewer can always see
    what the file actually said, whatever the outcome."""
    match_tier: MatchTier | None
    profile: BuiltInProfile | None
    """Set only when ``status`` is :attr:`MatchStatus.MATCHED`."""
    candidates: tuple[BuiltInProfile, ...] = ()
    """The tied entries when ``status`` is :attr:`MatchStatus.AMBIGUOUS`;
    empty otherwise. Never a "closest match" list -- an unknown schema
    names no candidates at all."""

    @property
    def matched(self) -> bool:
        return self.status is MatchStatus.MATCHED


def _entries_matching(
    registry: BankProfileRegistry, raw_bytes: bytes, tier: MatchTier
) -> tuple[BuiltInProfile, ...]:
    """Every entry whose declared header row this file has, at one tier.

    Each candidate is read under *its own* declared encoding and delimiter,
    so a profile declaring ``;``/``cp1252`` is tested honestly instead of
    against a guessed read. An entry whose declared read cannot even decode
    these bytes simply does not match.
    """
    matches: list[BuiltInProfile] = []
    for entry in registry:
        try:
            observed = read_signature(
                raw_bytes,
                delimiter=entry.profile.delimiter,
                encoding=entry.profile.encoding,
            )
        except BankSchemaReadError:
            continue
        expected = entry.signature
        hit = (
            observed.matches_exactly(expected)
            if tier is MatchTier.EXACT
            else observed.matches_normalized(expected)
        )
        if hit:
            matches.append(entry)
    return tuple(matches)


def inspect_bank_csv(
    raw_bytes: bytes, registry: BankProfileRegistry
) -> BankSchemaInspection:
    """Compare an uploaded statement's header row against the registry.

    Read-only in the strict sense: no canonical record, no case, no batch,
    no ledger write, no provider call. Reads the first row of the file and
    nothing else.

    Tiers are tried strongest-first and never mixed: if any entry matches
    exactly, the safe-normalized tier is not consulted at all, so a
    normalized near-neighbour can never dilute an exact match into an
    ambiguity. Within a tier, one match selects, two or more fail closed.
    """
    try:
        observed = read_signature(
            raw_bytes, delimiter=DISPLAY_DELIMITER, encoding=DISPLAY_ENCODING
        )
    except BankSchemaReadError:
        # Undisplayable bytes are still not a match for anything, and
        # saying so is more useful than an error page: the manual-profile
        # path (which declares its own encoding) remains available.
        observed = SchemaSignature(
            raw_headers=(), normalized_headers=(), delimiter=DISPLAY_DELIMITER,
            encoding=DISPLAY_ENCODING,
        )

    for tier in (MatchTier.EXACT, MatchTier.SAFE_NORMALIZED):
        matches = _entries_matching(registry, raw_bytes, tier)
        if len(matches) == 1:
            return BankSchemaInspection(
                status=MatchStatus.MATCHED,
                observed=observed,
                match_tier=tier,
                profile=matches[0],
            )
        if len(matches) > 1:
            return BankSchemaInspection(
                status=MatchStatus.AMBIGUOUS,
                observed=observed,
                match_tier=tier,
                profile=None,
                candidates=matches,
            )

    return BankSchemaInspection(
        status=MatchStatus.UNKNOWN, observed=observed, match_tier=None, profile=None
    )


@dataclass(frozen=True)
class BankProfileSelection:
    """How the profile used for one run was chosen -- a provenance fact.

    Recorded so a reviewer reading a batch months later can answer, without
    re-deriving anything: which profile, which version, was it a reviewed
    built-in or an uploaded one, which match tier justified selecting it,
    and what header signature the file actually presented.

    Successful selection is provenance, not an ingestion *issue*: it is
    written to the ingestion audit under its own event type and is
    deliberately outside the issue allowlist in
    :mod:`finrecon.api.service`.
    """

    profile_id: str
    selection_mode: ProfileSelectionMode
    match_tier: MatchTier | None = None
    version: str | None = None
    label: str | None = None
    verification: str | None = None
    schema_signature: str | None = None
    raw_headers: tuple[str, ...] = ()

    @classmethod
    def manual(cls, profile_id: str) -> BankProfileSelection:
        """An uploaded profile JSON: no tier, no registry metadata."""
        return cls(profile_id=profile_id, selection_mode=ProfileSelectionMode.MANUAL_UPLOAD)

    @classmethod
    def detected(
        cls, entry: BuiltInProfile, inspection: BankSchemaInspection
    ) -> BankProfileSelection:
        return cls(
            profile_id=entry.profile_id,
            selection_mode=ProfileSelectionMode.BUILT_IN,
            match_tier=inspection.match_tier,
            version=entry.version,
            label=entry.label,
            verification=entry.verification.value,
            schema_signature=inspection.observed.digest,
            raw_headers=inspection.observed.raw_headers,
        )

    def audit_payload(self) -> dict:
        """JSON-safe provenance body for the ingestion audit trail."""
        return {
            "profile_id": self.profile_id,
            "selection_mode": self.selection_mode.value,
            "match_tier": self.match_tier.value if self.match_tier else None,
            "profile_version": self.version,
            "label": self.label,
            "verification": self.verification,
            "schema_signature": self.schema_signature,
            "raw_headers": list(self.raw_headers),
        }


class BuiltInProfileVerificationError(RuntimeError):
    """A requested built-in profile is not what this upload actually is.

    Carries ``code`` so an HTTP boundary can distinguish "no such profile"
    from "that profile does not match these bytes" without parsing prose.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def resolve_verified_built_in(
    profile_id: str, raw_bytes: bytes, registry: BankProfileRegistry
) -> tuple[BuiltInProfile, BankSchemaInspection]:
    """Resolve a client-supplied ``profile_id``, re-checking the bytes.

    A ``profile_id`` arriving from a browser is a *claim*, not a fact.
    Without this check a client could pair ``profile_id`` with an entirely
    unrelated CSV and have its columns read under someone else's mapping --
    bypassing detection completely and silently mis-stating money. So the
    server re-runs the same inspection and requires that detection, on its
    own, would have selected exactly this profile.

    Requiring *selection* (rather than merely "this entry is among the
    matches") also means an ambiguous upload cannot be resolved by the
    client picking a side over the wire; ambiguity stays a human decision,
    made explicitly, exactly as it is in the UI.
    """
    if registry.get(profile_id) is None:
        raise BuiltInProfileVerificationError(
            "unknown_built_in_profile",
            f"No built-in bank profile {profile_id!r} ships with this build.",
        )
    inspection = inspect_bank_csv(raw_bytes, registry)
    if inspection.profile is None or inspection.profile.profile_id != profile_id:
        raise BuiltInProfileVerificationError(
            "bank_profile_mismatch",
            f"The uploaded bank statement does not match built-in profile "
            f"{profile_id!r} (schema inspection returned {inspection.status.value}). "
            "Re-inspect the file, or supply a manual bank profile.",
        )
    return inspection.profile, inspection


__all__ = [
    "DISPLAY_DELIMITER",
    "DISPLAY_ENCODING",
    "BankProfileSelection",
    "BankSchemaInspection",
    "BuiltInProfileVerificationError",
    "MatchStatus",
    "MatchTier",
    "inspect_bank_csv",
    "resolve_verified_built_in",
]
