"""Unknown-schema column mapping: propose, validate, and hand to a human.

The pipeline this package implements, and the authority at each step:

    clean bank CSV
        -> sample.py       bounded excerpt        (no authority; read-only)
        -> service.py       model proposes         (no authority; suggests)
        -> validation.py    deterministic checks   (veto only; never repairs)
        -> [ the human reviews, edits, names and confirms ]
        -> ledger           persisted mapping      (authority: human_confirmed)

Read the arrow between validation and the ledger as a wall. Nothing in this
package writes a mapping, and nothing in it can: no module here is given a
ledger store, and the saved-mapping writer
(:mod:`finrecon.ledger.bank_mappings`) is reached only from the API endpoint
a person's confirmation triggers. A proposal that nobody confirmed leaves no
trace beyond the HTTP response it was returned in.

The deliberate asymmetry: :mod:`.validation` may *reject* a mapping outright
but may never substitute a better one. A mapping that reached the ledger by
being quietly corrected is a mapping no human reviewed, and the whole point
of this package is that a human reviewed it.

Recognition of an *already*-confirmed mapping is not here -- that is
:mod:`finrecon.adapters.bank.schema`, which does it deterministically by
header signature and reaches no model at all.
"""

from __future__ import annotations

from .formats import (
    SUPPORTED_VALUE_DATE_FORMATS,
    FormatAmbiguity,
    format_ambiguity,
    format_options,
    is_supported_value_date_format,
)
from .proposal import (
    PROPOSAL_SCHEMA_VERSION,
    PROPOSAL_TOOL_NAME,
    MappingProposal,
    ProposedMapping,
    proposal_tool_schema,
)
from .sample import (
    MAX_CELL_CHARS,
    MAX_SAMPLE_CHARS,
    MAX_SAMPLE_ROWS,
    BankCsvSample,
    BankCsvSampleError,
    read_sample,
)
from .service import (
    MAX_MODEL_CALLS,
    ProposalAttempt,
    ProposalFailure,
    ProposalOutcome,
    propose_mapping,
)
from .validation import MappingIssue, MappingValidation, validate_mapping_payload

__all__ = [
    "MAX_CELL_CHARS",
    "MAX_MODEL_CALLS",
    "MAX_SAMPLE_CHARS",
    "MAX_SAMPLE_ROWS",
    "PROPOSAL_SCHEMA_VERSION",
    "PROPOSAL_TOOL_NAME",
    "SUPPORTED_VALUE_DATE_FORMATS",
    "BankCsvSample",
    "BankCsvSampleError",
    "FormatAmbiguity",
    "MappingIssue",
    "MappingProposal",
    "MappingValidation",
    "ProposalAttempt",
    "ProposalFailure",
    "ProposalOutcome",
    "ProposedMapping",
    "format_ambiguity",
    "format_options",
    "is_supported_value_date_format",
    "proposal_tool_schema",
    "propose_mapping",
    "read_sample",
    "validate_mapping_payload",
]
