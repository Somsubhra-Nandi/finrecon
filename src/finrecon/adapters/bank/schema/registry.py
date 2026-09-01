"""The read-only registry of bank profiles that ship with FinRecon.

A built-in profile is authoritative for exactly one reason: a human wrote
the column mapping down, reviewed it, and versioned it. The registry adds
no intelligence on top of that -- it is a static directory of JSON
artifacts loaded at startup, and nothing at runtime can add to it, edit it,
or reorder it. There is no database, no editing surface, no sharing model,
and no persistence migration; user-confirmed profiles are a later phase
and deliberately have no representation here.

**Immutable by convention.** A registry artifact is a versioned statement
about one schema. A schema change is a *new* artifact
(``..._v1`` -> ``..._v2``), never an edit to the old one: historical audit
rows name a ``profile_id``/``version``, and silently changing what that
pair means would rewrite the meaning of already-recorded evidence.

**Trust is stated, never implied.** :class:`ProfileVerification` forces
every entry to say how well its schema is actually evidenced, and the API
and UI surface that verbatim. The repository does not currently contain
trustworthy documentation for any real bank's CSV export (see
``src/finrecon/adapters/bank/README.md``, "ICICI: not shipped, and why"),
so no entry here claims to be one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from finrecon.json_text import decode_json_bytes

from ..csv_profile import BankCsvProfile
from ..profile_json import BankProfileFormatError, profile_from_payload
from .signature import SchemaSignature, signature_from_headers

BUILT_IN_PROFILE_DIR = Path(__file__).resolve().parents[1] / "profiles"

REGISTRY_ARTIFACT_VERSION = 1
"""Schema version of the *artifact envelope* (not of any bank profile).

Bumped only if the envelope's own fields change; an artifact declaring
anything else is rejected rather than read on a best-effort basis.
"""


class ProfileVerification(str, Enum):
    """How well a built-in profile's schema is actually evidenced.

    Kept honest on purpose -- this string reaches the UI, and claiming
    more than the evidence supports is exactly the failure mode automatic
    detection makes dangerous.
    """

    VENDOR_VERIFIED = "vendor_verified"
    """Schema taken from the bank's own published documentation, or from a
    real export sample checked into this repository. Nothing ships at this
    level today."""

    PARTIALLY_VERIFIED = "partially_verified"
    """Schema evidenced by something real but incomplete -- e.g. one
    observed export whose edge cases are undocumented. Nothing ships at
    this level today."""

    DEMO_FIXTURE = "demo_fixture"
    """A synthetic schema authored inside this repository, whose header row
    is verifiable here because the file it describes lives here too. Honest
    about being a demo: it is not a claim about any real bank."""


class ProfileSelectionMode(str, Enum):
    """Where the profile used for a run came from."""

    BUILT_IN = "built_in"
    """Resolved from this registry and re-verified server-side against the
    uploaded bytes."""

    MANUAL_UPLOAD = "manual_upload"
    """Supplied as profile JSON with the request -- the existing escape
    hatch, unchanged."""


class BankProfileRegistryError(RuntimeError):
    """A registry artifact is unloadable, or the registry is inconsistent.

    Raised at load time, never swallowed: a registry that silently dropped
    a malformed artifact would make automatic selection depend on which
    profiles happened to parse.
    """


@dataclass(frozen=True)
class BuiltInProfile:
    """One reviewed, versioned, shipped profile plus its schema metadata.

    Frozen, and every collection field is a tuple, so a caller holding an
    entry cannot mutate what the registry hands the next caller.
    """

    profile_id: str
    label: str
    version: str
    verification: ProfileVerification
    description: str
    evidence: str
    """Why this schema is believed to be correct, in prose, for review and
    for the UI's disclosure line. Required -- an entry with no stated
    evidence has no business being authoritative."""
    expected_headers: tuple[str, ...]
    profile: BankCsvProfile

    @property
    def signature(self) -> SchemaSignature:
        """The header row this entry expects, under its declared read."""
        return signature_from_headers(
            self.expected_headers,
            delimiter=self.profile.delimiter,
            encoding=self.profile.encoding,
        )

    def metadata(self) -> dict:
        """The UI/audit view of this entry -- never the column mapping."""
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "version": self.version,
            "verification": self.verification.value,
            "description": self.description,
            "evidence": self.evidence,
            "expected_headers": list(self.expected_headers),
            "signature": self.signature.digest,
        }


def _require(payload: Mapping[str, object], key: str, source: Path) -> object:
    if key not in payload:
        raise BankProfileRegistryError(f"{source}: registry artifact is missing {key!r}")
    return payload[key]


def load_built_in_profile(path: Path) -> BuiltInProfile:
    """Load and fully validate one registry artifact. Fails loudly."""
    try:
        payload = json.loads(decode_json_bytes(path.read_bytes()))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BankProfileRegistryError(f"{path}: not readable as UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BankProfileRegistryError(f"{path}: registry artifact must be a JSON object")

    declared_version = _require(payload, "registry_artifact_version", path)
    if declared_version != REGISTRY_ARTIFACT_VERSION:
        raise BankProfileRegistryError(
            f"{path}: registry_artifact_version is {declared_version!r}, this build "
            f"reads {REGISTRY_ARTIFACT_VERSION!r}"
        )

    verification_raw = _require(payload, "verification", path)
    try:
        verification = ProfileVerification(verification_raw)
    except ValueError as exc:
        valid = [member.value for member in ProfileVerification]
        raise BankProfileRegistryError(
            f"{path}: verification must be one of {valid}, got {verification_raw!r}"
        ) from exc

    headers = _require(payload, "expected_headers", path)
    if (
        not isinstance(headers, list)
        or not headers
        or not all(isinstance(header, str) for header in headers)
    ):
        raise BankProfileRegistryError(
            f"{path}: expected_headers must be a non-empty list of strings"
        )

    profile_payload = _require(payload, "profile", path)
    if not isinstance(profile_payload, dict):
        raise BankProfileRegistryError(f"{path}: profile must be a JSON object")
    try:
        profile = profile_from_payload(profile_payload)
    except BankProfileFormatError as exc:
        raise BankProfileRegistryError(f"{path}: invalid bank profile: {exc}") from exc

    entry_id = _require(payload, "profile_id", path)
    if entry_id != profile.profile_id:
        # The envelope's id is what the API and audit rows key on; the
        # embedded profile's id is what namespaces every bank_record_id it
        # produces. Letting the two drift would make a run's records
        # untraceable to the registry entry that produced them.
        raise BankProfileRegistryError(
            f"{path}: profile_id {entry_id!r} disagrees with the embedded "
            f"profile's profile_id {profile.profile_id!r}"
        )

    missing_declared = profile.declared_columns() - set(headers)
    if missing_declared:
        # Caught here rather than at the first upload: an entry whose own
        # declared header row cannot satisfy its own column mapping can
        # never match anything, and shipping it is a build-time mistake.
        raise BankProfileRegistryError(
            f"{path}: profile declares columns {sorted(missing_declared)} absent "
            f"from expected_headers {headers}"
        )

    for field_name in ("label", "version", "description", "evidence"):
        value = _require(payload, field_name, path)
        if not isinstance(value, str) or not value.strip():
            raise BankProfileRegistryError(
                f"{path}: {field_name} must be a non-empty string"
            )

    return BuiltInProfile(
        profile_id=str(entry_id),
        label=str(payload["label"]),
        version=str(payload["version"]),
        verification=verification,
        description=str(payload["description"]),
        evidence=str(payload["evidence"]),
        expected_headers=tuple(headers),
        profile=profile,
    )


class BankProfileRegistry:
    """An immutable, ordered collection of built-in profiles.

    Ordering is by ``profile_id`` purely so listings are stable; nothing in
    detection ever uses position, because a positional tie-break is exactly
    the silent wrong answer ambiguity handling exists to prevent.
    """

    def __init__(self, entries: tuple[BuiltInProfile, ...]) -> None:
        by_id: dict[str, BuiltInProfile] = {}
        for entry in entries:
            if entry.profile_id in by_id:
                raise BankProfileRegistryError(
                    f"duplicate built-in profile_id {entry.profile_id!r}; a schema "
                    "change must ship as a new versioned profile_id, never as a "
                    "second artifact reusing the old one"
                )
            by_id[entry.profile_id] = entry
        self._by_id = by_id
        self._ordered = tuple(by_id[key] for key in sorted(by_id))

    @classmethod
    def from_directory(cls, directory: Path) -> BankProfileRegistry:
        """Load every ``*.json`` artifact in ``directory``, in filename order.

        A missing directory yields an empty registry (a build with no
        shipped profiles is legitimate -- everything simply falls through
        to the manual-profile path). An unreadable *artifact* is fatal.
        """
        if not directory.is_dir():
            return cls(())
        return cls(
            tuple(load_built_in_profile(path) for path in sorted(directory.glob("*.json")))
        )

    def __len__(self) -> int:
        return len(self._ordered)

    def __iter__(self) -> Iterator[BuiltInProfile]:
        return iter(self._ordered)

    def __contains__(self, profile_id: object) -> bool:
        return profile_id in self._by_id

    @property
    def entries(self) -> tuple[BuiltInProfile, ...]:
        """Every entry, as a tuple -- a caller cannot append to the registry."""
        return self._ordered

    def get(self, profile_id: str) -> BuiltInProfile | None:
        return self._by_id.get(profile_id)

    def require(self, profile_id: str) -> BuiltInProfile:
        entry = self._by_id.get(profile_id)
        if entry is None:
            raise KeyError(profile_id)
        return entry


@lru_cache(maxsize=1)
def built_in_registry() -> BankProfileRegistry:
    """The profiles shipped with this build. Loaded once, then shared.

    Cached because the artifacts are static files inside the installed
    package; there is no reload path on purpose, since a profile changing
    underneath a running server is precisely the mutable-profile problem
    this phase avoids.
    """
    return BankProfileRegistry.from_directory(BUILT_IN_PROFILE_DIR)


__all__ = [
    "BUILT_IN_PROFILE_DIR",
    "REGISTRY_ARTIFACT_VERSION",
    "BankProfileRegistry",
    "BankProfileRegistryError",
    "BuiltInProfile",
    "ProfileSelectionMode",
    "ProfileVerification",
    "built_in_registry",
    "load_built_in_profile",
]
