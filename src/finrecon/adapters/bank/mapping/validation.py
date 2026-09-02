"""Deterministic validation of a candidate mapping, against the file itself.

Everything in this module runs locally, with no model in the loop, and it
runs on *every* mapping regardless of where the mapping came from -- a model
proposal, a human's edit of a proposal, or a mapping typed from scratch by
someone who never asked for a proposal at all. That is deliberate: a
validator that only guards the model's path would leave the human path
unchecked, and the human path is the one that gets persisted.

What is checked, and why each check is here rather than left to the parser:

* **Columns exist.** The parser already fails fast on a declared column
  absent from the file, but it does so at ingestion -- after a batch has been
  opened. Checking here means an impossible mapping is never offered for
  confirmation and never saved.
* **Debit is not credit.** :class:`..csv_profile.DebitCreditColumns` refuses
  it too; catching it here turns a construction error into a field-level
  message the editor can point at.
* **The date format actually parses the sampled dates.** A format that
  cannot read the file's own first rows is wrong, and saying so from the
  data is cheaper and more honest than any model's confidence.
* **The date format is not ambiguous, or is flagged as needing a choice.**
  See :mod:`.formats`. This never blocks the mapping; it marks the field as
  requiring an explicit human decision.
* **The money model is not contradicted by the sample.** Under
  ``empty_only``, a sampled row with both sides populated is a row the
  parser would reject; under ``empty_or_zero``, a sample where one side is
  zero-filled on every row is evidence the marker is right, and a sample
  where *neither* side is ever populated is evidence the columns are wrong.
  These are reported as findings on the observable sample, never as a
  re-derived mapping.
* **Profile construction succeeds.** The last check is simply building the
  real frozen declaration, so nothing can be confirmed that the production
  reader would refuse.

**No repair.** This module reports; it never substitutes a column it thinks
would work better. A silently repaired mapping is a mapping no human
reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ..csv_profile import BankCsvProfile, InactiveSideMarker
from ..profile_json import BankProfileFormatError, profile_from_payload
from .formats import FormatAmbiguity, format_ambiguity, is_supported_value_date_format
from .sample import BankCsvSample


@dataclass(frozen=True)
class MappingIssue:
    """One problem with a candidate mapping, addressed to one field."""

    field: str
    """The mapping field this is about, in wire naming, so the editor can
    highlight the control the operator has to fix."""
    code: str
    message: str

    def payload(self) -> dict:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class MappingValidation:
    """The verdict on one candidate mapping, plus what needs a human.

    ``errors`` make the mapping unusable. ``warnings`` do not: they are
    observations from the sample that a reviewer should see before
    confirming, and a mapping may legitimately be confirmed over them (a
    five-row excerpt is not proof about a whole statement).
    """

    errors: tuple[MappingIssue, ...] = ()
    warnings: tuple[MappingIssue, ...] = ()
    fields_requiring_human_choice: tuple[str, ...] = ()
    """Fields the sample cannot settle. The confirmation UI must require an
    explicit choice for each; the server does not accept one on trust."""
    date_format: FormatAmbiguity | None = None
    profile: BankCsvProfile | None = None
    """The constructed declaration, present only when there are no errors."""

    @property
    def ok(self) -> bool:
        return not self.errors

    def payload(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [issue.payload() for issue in self.errors],
            "warnings": [issue.payload() for issue in self.warnings],
            "fields_requiring_human_choice": list(self.fields_requiring_human_choice),
            "date_format": (
                {
                    "proposed": self.date_format.proposed,
                    "plausible": list(self.date_format.plausible),
                    "contradicted": self.date_format.contradicted,
                    "ambiguous_with": list(self.date_format.ambiguous_with),
                    "evidence_rows": self.date_format.evidence_rows,
                    "requires_human_choice": self.date_format.requires_human_choice,
                }
                if self.date_format
                else None
            ),
        }


@dataclass
class _Collector:
    errors: list[MappingIssue] = field(default_factory=list)
    warnings: list[MappingIssue] = field(default_factory=list)

    def error(self, field_name: str, code: str, message: str) -> None:
        self.errors.append(MappingIssue(field_name, code, message))

    def warn(self, field_name: str, code: str, message: str) -> None:
        self.warnings.append(MappingIssue(field_name, code, message))


def _money_text(value: str) -> Decimal | None:
    """Parse one sampled money cell permissively enough to *classify* it.

    Thousands separators are tolerated here because a profile may or may not
    declare one and this is a plausibility check, not the ingestion parse.
    The real conversion is still
    :meth:`finrecon.models.money.Paise.from_rupees` at ingestion; nothing
    read here reaches a canonical record.
    """
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _check_money(
    collector: _Collector, payload: dict, sample: BankCsvSample, headers: set[str]
) -> None:
    money = payload.get("money_columns")
    if not isinstance(money, dict):
        collector.error("money_columns", "missing_money_columns", "A money model is required.")
        return
    kind = money.get("kind")
    if kind == "debit_credit":
        debit = money.get("debit_column")
        credit = money.get("credit_column")
        for field_name, value in (("debit_column", debit), ("credit_column", credit)):
            if not value:
                collector.error(field_name, "missing_column", "This column must be chosen.")
            elif value not in headers:
                collector.error(
                    field_name,
                    "unknown_column",
                    f"{value!r} is not a column in this statement.",
                )
        if debit and credit and debit == credit:
            collector.error(
                "credit_column",
                "debit_equals_credit",
                "The debit and credit columns must be two different columns; one "
                "column cannot declare both sides.",
            )
        marker_raw = money.get("inactive_side_marker", InactiveSideMarker.EMPTY_ONLY.value)
        try:
            marker = InactiveSideMarker(marker_raw)
        except ValueError:
            collector.error(
                "inactive_side_marker",
                "invalid_enum",
                f"{marker_raw!r} is not a supported inactive-side behaviour.",
            )
            return
        if not (debit in headers and credit in headers) or debit == credit:
            return
        _check_debit_credit_sample(collector, sample, str(debit), str(credit), marker)
        return
    if kind == "amount_direction":
        amount = money.get("amount_column")
        direction = money.get("direction_column")
        for field_name, value in (
            ("amount_column", amount),
            ("direction_column", direction),
        ):
            if not value:
                collector.error(field_name, "missing_column", "This column must be chosen.")
            elif value not in headers:
                collector.error(
                    field_name,
                    "unknown_column",
                    f"{value!r} is not a column in this statement.",
                )
        credit_values = money.get("credit_values") or []
        debit_values = money.get("debit_values") or []
        if not credit_values or not debit_values:
            collector.error(
                "credit_values",
                "missing_direction_values",
                "Both the credit and the debit marker values must be declared; "
                "direction is never inferred from an amount's sign.",
            )
            return
        overlap = sorted(set(credit_values) & set(debit_values))
        if overlap:
            collector.error(
                "credit_values",
                "overlapping_direction_values",
                f"{overlap} appear as both credit and debit markers.",
            )
        if direction in headers:
            observed = {v.strip() for v in sample.column(str(direction)) if v.strip()}
            unknown = sorted(observed - set(credit_values) - set(debit_values))
            if unknown:
                collector.warn(
                    "direction_column",
                    "unrecognized_direction_value_in_sample",
                    f"The sampled rows contain direction marker(s) {unknown} that are "
                    "in neither list; rows carrying them would be rejected.",
                )
        return
    collector.error(
        "money_columns",
        "invalid_money_kind",
        f"Money model must be 'debit_credit' or 'amount_direction', got {kind!r}.",
    )


def _check_debit_credit_sample(
    collector: _Collector,
    sample: BankCsvSample,
    debit: str,
    credit: str,
    marker: InactiveSideMarker,
) -> None:
    """Test the declared debit/credit semantics against the sampled rows.

    Reports contradictions only. It never flips the marker or swaps the
    columns: which side is the debit is a claim about the bank's convention,
    not something five rows establish, and a validator that "fixed" it would
    be inferring meaning under a name that promises not to.
    """
    debit_cells = sample.column(debit)
    credit_cells = sample.column(credit)
    if not debit_cells and not credit_cells:
        collector.warn(
            "money_columns",
            "no_sampled_rows",
            "No data rows were sampled, so the debit/credit convention could not "
            "be checked against the file.",
        )
        return

    both_populated = 0
    neither_populated = 0
    zero_filled_rows = 0
    malformed: list[str] = []
    for debit_raw, credit_raw in zip(debit_cells, credit_cells):
        debit_text, credit_text = debit_raw.strip(), credit_raw.strip()
        debit_value = _money_text(debit_text)
        credit_value = _money_text(credit_text)
        for column, text, value in (
            (debit, debit_text, debit_value),
            (credit, credit_text, credit_value),
        ):
            if text and value is None:
                malformed.append(f"{column}={text!r}")
        if debit_text and credit_text:
            if marker is InactiveSideMarker.EMPTY_ONLY:
                both_populated += 1
            elif (debit_value == 0) != (credit_value == 0):
                zero_filled_rows += 1
            elif debit_value != 0 and credit_value != 0:
                both_populated += 1
            else:
                neither_populated += 1
        elif not debit_text and not credit_text:
            neither_populated += 1

    if malformed:
        collector.warn(
            "money_columns",
            "malformed_money_in_sample",
            "The sampled rows contain values these columns cannot convert to an "
            f"exact amount ({', '.join(sorted(set(malformed))[:4])}); rows like "
            "them would be rejected at ingestion.",
        )
    if both_populated:
        detail = (
            "this mapping declares that an inactive side is empty, so such rows "
            "would be rejected"
            if marker is InactiveSideMarker.EMPTY_ONLY
            else "such rows carry two non-zero amounts and would be rejected"
        )
        collector.error(
            "inactive_side_marker",
            "money_model_contradicted_by_sample",
            f"{both_populated} of the sampled rows populate both {debit!r} and "
            f"{credit!r}: {detail}. If this source zero-fills the side a row does "
            "not use, choose 'Empty or zero'.",
        )
    if neither_populated == len(debit_cells) and debit_cells:
        collector.error(
            "money_columns",
            "no_amount_in_sample",
            f"None of the sampled rows carries an amount in {debit!r} or "
            f"{credit!r}; these are probably not this statement's money columns.",
        )
    elif neither_populated:
        collector.warn(
            "money_columns",
            "rows_without_amount_in_sample",
            f"{neither_populated} of the sampled rows carry no amount in either "
            "column; rows like them are not financial movements and would be "
            "recorded as ingestion issues.",
        )
    if marker is InactiveSideMarker.EMPTY_OR_ZERO and not zero_filled_rows:
        collector.warn(
            "inactive_side_marker",
            "zero_fill_not_observed",
            "This mapping declares that the inactive side is zero-filled, but no "
            "sampled row shows a zero beside an amount. Confirm the convention.",
        )


def validate_mapping_payload(
    payload: dict, sample: BankCsvSample
) -> MappingValidation:
    """Validate one profile payload against a bounded sample of its own file.

    ``payload`` is a :mod:`..profile_json` mapping -- the same wire shape a
    manual profile upload carries -- so exactly one reader is used for
    proposals, edits and hand-written mappings alike.
    """
    collector = _Collector()
    headers = {h for h in sample.raw_headers if h}
    requires_choice: list[str] = []

    for field_name in ("value_date_column", "narration_column"):
        value = payload.get(field_name)
        if not value:
            collector.error(field_name, "missing_column", "This column must be chosen.")
        elif value not in headers:
            collector.error(
                field_name,
                "unknown_column",
                f"{value!r} is not a column in this statement.",
            )

    reference = payload.get("reference_id_column")
    if reference is not None and reference not in headers:
        collector.error(
            "reference_id_column",
            "unknown_column",
            f"{reference!r} is not a column in this statement. Choose 'None' if "
            "this statement has no reference column.",
        )

    date_format = payload.get("value_date_format")
    ambiguity: FormatAmbiguity | None = None
    if not date_format:
        collector.error(
            "value_date_format", "missing_date_format", "A date format must be chosen."
        )
    elif not is_supported_value_date_format(str(date_format)):
        collector.error(
            "value_date_format",
            "unsupported_date_format",
            f"{date_format!r} is not a value-date format FinRecon supports here. "
            "Choose one of the offered formats, or declare it in a manual bank "
            "profile JSON.",
        )
    elif payload.get("value_date_column") in headers:
        samples = sample.column(str(payload["value_date_column"]))
        ambiguity = format_ambiguity(str(date_format), samples)
        if ambiguity.contradicted:
            plausible = (
                f" The sampled values parse under {list(ambiguity.plausible)}."
                if ambiguity.plausible
                else " No supported format parses every sampled value."
            )
            collector.error(
                "value_date_format",
                "date_format_contradicted_by_sample",
                f"{date_format!r} does not parse the sampled values in "
                f"{payload['value_date_column']!r}.{plausible}",
            )
        elif ambiguity.ambiguous_with:
            collector.warn(
                "value_date_format",
                "date_format_ambiguous",
                f"The sampled dates parse equally well under {date_format!r} and "
                f"{list(ambiguity.ambiguous_with)}; nothing in this file "
                "distinguishes them. Confirm which one this bank uses.",
            )
            requires_choice.append("value_date_format")
        elif ambiguity.evidence_rows == 0:
            collector.warn(
                "value_date_format",
                "date_format_unverified",
                "No sampled value-date rows were available, so this format could "
                "not be checked against the file.",
            )
            requires_choice.append("value_date_format")
        if ambiguity is not None and not ambiguity.contradicted and ambiguity.unparsed_rows:
            # The format reads this statement; some rows in it are junk. That
            # is an ingestion issue waiting to happen, not a wrong mapping,
            # and it is the operator's call whether it matters.
            collector.warn(
                "value_date_format",
                "date_unparseable_rows_in_sample",
                f"{ambiguity.unparsed_rows} of the {ambiguity.evidence_rows} sampled "
                f"value-date rows cannot be read as {date_format!r}. The mapping is "
                "usable; rows like them will be recorded as ingestion issues rather "
                "than reconciled."
            )

    _check_money(collector, payload, sample, headers)

    profile: BankCsvProfile | None = None
    if not collector.errors:
        try:
            profile = profile_from_payload(payload)
        except BankProfileFormatError as exc:
            collector.error("mapping", "invalid_profile", str(exc))
        else:
            missing = sorted(profile.declared_columns() - headers)
            if missing:
                collector.error(
                    "mapping",
                    "unknown_column",
                    f"The mapping declares column(s) {missing} that this statement "
                    "does not have.",
                )
                profile = None

    return MappingValidation(
        errors=tuple(collector.errors),
        warnings=tuple(collector.warnings),
        fields_requiring_human_choice=tuple(requires_choice),
        date_format=ambiguity,
        profile=profile if not collector.errors else None,
    )


__all__ = [
    "MappingIssue",
    "MappingValidation",
    "validate_mapping_payload",
]
