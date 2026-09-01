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
    MatchStatus,
    MatchTier,
    inspect_bank_csv,
    resolve_verified_built_in,
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
from .signature import (
    BankSchemaReadError,
    SchemaSignature,
    read_signature,
    signature_from_headers,
)

__all__ = [
    "BUILT_IN_PROFILE_DIR",
    "BankProfileRegistry",
    "BankProfileRegistryError",
    "BankProfileSelection",
    "BankSchemaInspection",
    "BankSchemaReadError",
    "BuiltInProfile",
    "BuiltInProfileVerificationError",
    "MatchStatus",
    "MatchTier",
    "ProfileSelectionMode",
    "ProfileVerification",
    "SchemaSignature",
    "built_in_registry",
    "encoding_family",
    "inspect_bank_csv",
    "load_built_in_profile",
    "normalize_header",
    "normalize_headers",
    "read_signature",
    "resolve_verified_built_in",
    "signature_from_headers",
]
