"""Read-only recognition of an already-reviewed bank schema.

This is *detection*, and it is worth being pedantic about what that does
and does not mean, because three different things are easy to confuse:

1. **Detection** -- "the header row in this file is the header row profile
   ``X`` declares". That is all this module does.
2. **Inference** -- "these columns probably mean debit and credit". Not
   here. A profile's debit column, credit column, value-date format,
   narration, reference and inactive-side semantics are only ever read off
   a human-reviewed artifact -- a shipped registry entry, or a mapping this
   deployment's operator confirmed and saved -- never derived from a file.
   Proposing a mapping for a schema nobody has reviewed is a separate,
   bounded task that lives in
   :mod:`finrecon.adapters.bank.mapping.service` and never reaches this
   module.
3. **Authorization** -- "so go ahead and reconcile". Also not here: this
   module returns a finding, and the caller decides.

Nothing in this path builds a ``BankRecord``, opens a batch, touches the
ledger, or reaches a model. There is no prompt, no provider, no confidence
score and no ranking -- a schema either equals a reviewed one or it does
not.

**Fail closed.** Two entries matching at the same strongest tier is
:attr:`MatchStatus.AMBIGUOUS`, and no tie-break exists to resolve it: not
newest version, not highest version, not alphabetical, not registration
order, and not "prefer the operator's own saved mapping over a built-in".
No entry matching is :attr:`MatchStatus.UNKNOWN`, and the nearest entry is
deliberately not offered as a fallback. Both outcomes route the user to the
mapping-review path, where a human declares or confirms the mapping -- the
only thing that can safely resolve them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .registry import BankProfileRegistry, BuiltInProfile, ProfileSelectionMode
from .saved import SavedMappingEntry
from .signature import BankSchemaReadError, SchemaSignature, read_signature

MappingEntry = BuiltInProfile | SavedMappingEntry
"""Anything this module can recognise: a shipped profile or a saved mapping.

The two are treated identically here on purpose -- both are versioned column
mappings a human reviewed, and both are matched by header signature through
the same two tiers. See :mod:`.saved` for why a saved mapping's *name* plays
no part in that.
"""

MappingCorpus = BankProfileRegistry | object
"""Whatever the caller wants compared: anything iterable of :data:`MappingEntry`.

Deliberately structural. :class:`~.saved.CombinedMappingRegistry` is not a
:class:`~.registry.BankProfileRegistry` subclass, because a registry of
immutable shipped artifacts and a live view over the operator's ledger are
different things that should not inherit each other's guarantees; iteration
is the entire contract detection needs from either.
"""

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
    profile: MappingEntry | None
    """The selected entry -- a built-in profile or a saved mapping -- set only
    when ``status`` is :attr:`MatchStatus.MATCHED`."""
    candidates: tuple[MappingEntry, ...] = ()
    """The tied entries when ``status`` is :attr:`MatchStatus.AMBIGUOUS`;
    empty otherwise. Never a "closest match" list -- an unknown schema
    names no candidates at all."""

    @property
    def matched(self) -> bool:
        return self.status is MatchStatus.MATCHED


def _entries_matching(
    registry: MappingCorpus, raw_bytes: bytes, tier: MatchTier
) -> tuple[MappingEntry, ...]:
    """Every entry whose declared header row this file has, at one tier.

    Each candidate is read under *its own* declared encoding and delimiter,
    so a profile declaring ``;``/``cp1252`` is tested honestly instead of
    against a guessed read. An entry whose declared read cannot even decode
    these bytes simply does not match.
    """
    matches: list[MappingEntry] = []
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
    raw_bytes: bytes, registry: MappingCorpus
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
    mapping_id: str | None = None
    """The saved mapping's stable internal id, when one was used.

    Recorded alongside ``profile_id`` rather than instead of it: the
    ``profile_id`` names the exact *version* that read the statement (and
    namespaces its record IDs), while this names the logical mapping the
    operator recognises across versions. A reviewer needs both to answer
    "which mapping, and which revision of it"."""
    mapping_version: int | None = None
    provenance: str | None = None
    """``human_confirmed`` for a saved mapping. See :mod:`.saved`."""
    source: str | None = None
    llm_proposal: dict | None = None
    """The proposal metadata stored with the mapping, if a model proposed the
    mapping a human then confirmed. Copied into the audit trail as context,
    never as authority -- ``provenance`` is what says who decided."""

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
            source=ProfileSelectionMode.BUILT_IN.value,
        )

    @classmethod
    def saved_mapping(
        cls, entry: SavedMappingEntry, inspection: BankSchemaInspection
    ) -> BankProfileSelection:
        """A user-confirmed saved mapping, re-verified against these bytes.

        ``verification`` is deliberately absent. That field states how well a
        *shipped* profile's schema is evidenced by documentation this build
        can point at, and a claim of that kind about the operator's own
        mapping would be FinRecon vouching for something it has not seen.
        ``provenance`` carries the honest statement instead: a person here
        confirmed it.
        """
        return cls(
            profile_id=entry.profile_id,
            selection_mode=ProfileSelectionMode.USER_SAVED,
            match_tier=inspection.match_tier,
            version=entry.version,
            label=entry.name,
            schema_signature=inspection.observed.digest,
            raw_headers=inspection.observed.raw_headers,
            mapping_id=entry.mapping_id,
            mapping_version=entry.mapping_version,
            provenance=entry.provenance,
            source=entry.source,
            llm_proposal=entry.llm_proposal,
        )

    def audit_payload(self) -> dict:
        """JSON-safe provenance body for the ingestion audit trail.

        The pre-existing keys keep their exact names and meanings so audit
        rows written before saved mappings existed stay readable, and the
        saved-mapping keys are simply absent (``None``) for the two older
        paths.
        """
        return {
            "profile_id": self.profile_id,
            "selection_mode": self.selection_mode.value,
            "match_tier": self.match_tier.value if self.match_tier else None,
            "profile_version": self.version,
            "label": self.label,
            "verification": self.verification,
            "schema_signature": self.schema_signature,
            "raw_headers": list(self.raw_headers),
            "mapping_id": self.mapping_id,
            "mapping_version": self.mapping_version,
            "provenance": self.provenance,
            "source": self.source,
            "llm_proposal": self.llm_proposal,
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
    # The ``isinstance`` is not redundant with the id check: it keeps the
    # built-in path answering only about built-ins even if a saved mapping
    # ever came to share a ``profile_id``, so the two selection modes can
    # never be confused for one another in an audit row.
    if (
        not isinstance(inspection.profile, BuiltInProfile)
        or inspection.profile.profile_id != profile_id
    ):
        raise BuiltInProfileVerificationError(
            "bank_profile_mismatch",
            f"The uploaded bank statement does not match built-in profile "
            f"{profile_id!r} (schema inspection returned {inspection.status.value}). "
            "Re-inspect the file, or supply a manual bank profile.",
        )
    return inspection.profile, inspection


def resolve_verified_saved_mapping(
    mapping_id: str,
    raw_bytes: bytes,
    registry: MappingCorpus,
    active_entry: SavedMappingEntry | None,
) -> tuple[SavedMappingEntry, BankSchemaInspection]:
    """Resolve a client-supplied saved ``mapping_id``, re-checking the bytes.

    The same argument as :func:`resolve_verified_built_in`, and it matters
    more here rather than less. A saved mapping is exactly as capable of
    mis-stating money as a built-in -- more so, since nobody outside this
    deployment has reviewed it -- and it is reachable by an id the browser
    holds. So the id is treated as a *claim*: the server re-inspects the
    uploaded bytes against the whole corpus and requires that detection, on
    its own, would have selected this exact mapping.

    Two consequences worth stating, because they are the point:

    * A statement whose header row has changed since the mapping was saved
      does **not** get read under it. The mapping is not forced onto the
      file; the upload becomes unknown again and needs a new confirmation.
    * An ambiguous upload cannot be resolved by the client picking a side
      over the wire, because ``requires selection`` is stricter than "this
      mapping is among the matches".

    ``active_entry`` is passed in rather than looked up here so this module
    keeps no database dependency; ``None`` means the caller found no active
    version, which is a refusal rather than a fallback to an older one.
    """
    if active_entry is None:
        raise BuiltInProfileVerificationError(
            "unknown_bank_mapping",
            f"No active saved bank mapping {mapping_id!r} exists on this server.",
        )
    inspection = inspect_bank_csv(raw_bytes, registry)
    matched = inspection.profile
    if (
        not isinstance(matched, SavedMappingEntry)
        or matched.mapping_id != active_entry.mapping_id
        or matched.mapping_version != active_entry.mapping_version
    ):
        raise BuiltInProfileVerificationError(
            "bank_mapping_schema_mismatch",
            f"The uploaded bank statement does not match saved mapping "
            f"{active_entry.name!r} ({active_entry.version}); schema inspection "
            f"returned {inspection.status.value}. Confirm a mapping for this "
            "file's actual columns before reconciling it.",
        )
    return matched, inspection


__all__ = [
    "DISPLAY_DELIMITER",
    "DISPLAY_ENCODING",
    "BankProfileSelection",
    "BankSchemaInspection",
    "BuiltInProfileVerificationError",
    "MappingCorpus",
    "MappingEntry",
    "MatchStatus",
    "MatchTier",
    "inspect_bank_csv",
    "resolve_verified_built_in",
    "resolve_verified_saved_mapping",
]
