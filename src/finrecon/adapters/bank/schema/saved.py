"""User-confirmed mappings as first-class detection candidates.

A saved mapping and a built-in profile are authoritative for the *same*
reason: a human wrote the column mapping down, reviewed it, and it was
versioned. They differ only in who that human was and where the artifact
lives -- a file in this repository, or a row in the operator's own ledger.
So they participate in detection on equal terms and through the same two
tiers, rather than through a second, looser path.

:class:`SavedMappingEntry` therefore presents exactly the surface
:mod:`.detect` consumes from :class:`~.registry.BuiltInProfile`
(``profile_id``, ``expected_headers``, ``profile``, ``signature``), which is
why the detector needed no new branch to consider saved mappings: it already
iterates entries and compares signatures, and these are entries.

**The name is not part of matching.** Nothing in this module or in
:mod:`.detect` reads :attr:`SavedMappingEntry.name` to decide whether a file
matches. A mapping called "HDFC Current Account" recognises a statement
because its stored header signature equals the file's, and would recognise
exactly the same files if it were renamed "Client XYZ". Matching on a
user-chosen label would be bank-name inference with extra steps.

**Only active versions are candidates.** A superseded version stays in the
ledger forever so a historical batch can name it, and is never offered for a
new upload -- otherwise editing a mapping would leave two versions competing
to recognise the same schema, which is an ambiguity nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..csv_profile import BankCsvProfile
from .registry import BankProfileRegistry, BuiltInProfile
from .signature import SchemaSignature, signature_from_headers

SAVED_MAPPING_PROVENANCE = "human_confirmed"
"""The only provenance a persisted mapping may carry.

There is deliberately no ``llm_proposed`` value. A proposal that no person
confirmed is not a mapping, it is a suggestion, and it has no row.
"""

SAVED_MAPPING_SOURCE = "user_saved"


@dataclass(frozen=True)
class SavedMappingEntry:
    """One active (or historical) version of one user-confirmed mapping.

    Frozen, with tuple collections, for the same reason
    :class:`~.registry.BuiltInProfile` is: a caller holding an entry cannot
    mutate what the next caller receives.
    """

    mapping_id: str
    name: str
    """The operator's own label. Display and audit only -- never matching."""
    mapping_version: int
    profile_id: str
    """Namespaces every ``bank_record_id`` this mapping produces. Assigned by
    the store from ``(mapping_id, version)``, never chosen by a model, and
    distinct per version so a record is always traceable to the exact
    mapping that read it."""
    schema_signature: str
    expected_headers: tuple[str, ...]
    normalized_headers: tuple[str, ...]
    delimiter: str
    encoding: str
    profile: BankCsvProfile
    profile_payload: dict
    """The mapping's stored wire payload, exactly as it was confirmed.

    Kept beside the constructed :attr:`profile` rather than re-derived from
    it: the payload is what the operator confirmed and what the edit flow
    prefills from, and a round-trip through the frozen declaration would
    silently normalise away an omitted optional field.
    """
    status: str
    provenance: str = SAVED_MAPPING_PROVENANCE
    source: str = SAVED_MAPPING_SOURCE
    created_at: str | None = None
    llm_proposal: dict | None = None
    """Optional record that a model proposed the mapping a human then
    confirmed or corrected. Metadata for review; carries no authority, and
    its absence means nobody consulted a model, not that nobody confirmed."""

    @property
    def version(self) -> str:
        """The version as a display string, matching a built-in's ``vN``."""
        return f"v{self.mapping_version}"

    @property
    def label(self) -> str:
        """Alias for :attr:`name`, so UI code can treat entries uniformly."""
        return self.name

    @property
    def signature(self) -> SchemaSignature:
        """The header row this mapping expects, under its declared read.

        Recomputed from the stored headers rather than trusting the stored
        digest, so a signature can never disagree with the header row it is
        supposed to summarise.
        """
        return signature_from_headers(
            self.expected_headers,
            delimiter=self.profile.delimiter,
            encoding=self.profile.encoding,
        )

    @property
    def active(self) -> bool:
        return self.status == "active"

    def metadata(self) -> dict:
        """The UI/audit view of this entry. Never the column mapping itself."""
        return {
            "mapping_id": self.mapping_id,
            "name": self.name,
            "version": self.mapping_version,
            "profile_id": self.profile_id,
            "schema_signature": self.schema_signature,
            "expected_headers": list(self.expected_headers),
            "status": self.status,
            "provenance": self.provenance,
            "source": self.source,
            "created_at": self.created_at,
            "llm_proposal": self.llm_proposal,
        }


class CombinedMappingRegistry:
    """Built-in profiles and active saved mappings, as one detection corpus.

    Iterable, which is the entire contract :func:`.detect.inspect_bank_csv`
    needs. Built-ins are yielded first purely so listings are stable;
    detection never uses position, because a positional tie-break is exactly
    the silent wrong answer that ambiguity handling exists to prevent -- two
    entries matching at the same tier is
    :attr:`~.detect.MatchStatus.AMBIGUOUS` whether one of them is a built-in
    or not.
    """

    def __init__(
        self,
        built_ins: BankProfileRegistry,
        saved: tuple[SavedMappingEntry, ...] = (),
    ) -> None:
        self._built_ins = built_ins
        self._saved = tuple(entry for entry in saved if entry.active)

    def __iter__(self) -> Iterator[BuiltInProfile | SavedMappingEntry]:
        yield from self._built_ins
        yield from self._saved

    def __len__(self) -> int:
        return len(self._built_ins) + len(self._saved)

    @property
    def built_ins(self) -> BankProfileRegistry:
        return self._built_ins

    @property
    def saved(self) -> tuple[SavedMappingEntry, ...]:
        return self._saved

    def get(self, profile_id: str) -> BuiltInProfile | SavedMappingEntry | None:
        entry = self._built_ins.get(profile_id)
        if entry is not None:
            return entry
        return next((e for e in self._saved if e.profile_id == profile_id), None)


__all__ = [
    "SAVED_MAPPING_PROVENANCE",
    "SAVED_MAPPING_SOURCE",
    "CombinedMappingRegistry",
    "SavedMappingEntry",
]
