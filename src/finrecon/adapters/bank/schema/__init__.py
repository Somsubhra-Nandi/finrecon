"""Read-only bank-schema recognition, kept out of the parser on purpose.

``csv_parser.py`` is the module that must never guess. Putting recognition
logic next to it -- even careful recognition -- would blur the one boundary
that keeps that guarantee legible, so this is a separate pre-adapter layer:

    uploaded bytes -> normalize -> signature -> registry -> match result

and only a *matched* result hands a human-reviewed
:class:`~finrecon.adapters.bank.csv_profile.BankCsvProfile` to the parser,
which continues to do exactly what it always did with it.
"""

from __future__ import annotations

from .detect import (
    BankProfileSelection,
    BankSchemaInspection,
    BuiltInProfileVerificationError,
    MappingCorpus,
    MappingEntry,
    MatchStatus,
    MatchTier,
    inspect_bank_csv,
    resolve_verified_built_in,
    resolve_verified_saved_mapping,
)
from .normalize import encoding_family, normalize_header, normalize_headers
from .registry import (
    BUILT_IN_PROFILE_DIR,
    BankProfileRegistry,
    BankProfileRegistryError,
    BuiltInProfile,
    ProfileSelectionMode,
    ProfileVerification,
    built_in_registry,
    load_built_in_profile,
)
from .saved import (
    SAVED_MAPPING_PROVENANCE,
    SAVED_MAPPING_SOURCE,
    CombinedMappingRegistry,
    SavedMappingEntry,
)
from .signature import (
    BankSchemaReadError,
    SchemaSignature,
    read_signature,
    signature_from_headers,
)

__all__ = [
    "BUILT_IN_PROFILE_DIR",
    "SAVED_MAPPING_PROVENANCE",
    "SAVED_MAPPING_SOURCE",
    "BankProfileRegistry",
    "BankProfileRegistryError",
    "BankProfileSelection",
    "BankSchemaInspection",
    "BankSchemaReadError",
    "BuiltInProfile",
    "BuiltInProfileVerificationError",
    "CombinedMappingRegistry",
    "MappingCorpus",
    "MappingEntry",
    "MatchStatus",
    "MatchTier",
    "ProfileSelectionMode",
    "ProfileVerification",
    "SavedMappingEntry",
    "SchemaSignature",
    "built_in_registry",
    "encoding_family",
    "inspect_bank_csv",
    "load_built_in_profile",
    "normalize_header",
    "normalize_headers",
    "read_signature",
    "resolve_verified_built_in",
    "resolve_verified_saved_mapping",
    "signature_from_headers",
]
