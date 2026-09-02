"""Schema recognition: what may auto-select, and what must never.

The safety argument for automatic profile selection is entirely contained
in this file's negative cases. Detection is allowed to see past a BOM,
whitespace and letter case, because none of those change which column a
bank named. It must *not* see past punctuation, abbreviation, synonymy,
edit distance, column order, or a missing/extra column -- each of those is
a claim about meaning, and a wrong claim there reads somebody's debit
column as their credit column.

Ambiguity is the other half: two profiles matching equally well is a
question only a human can answer, so it fails closed with no tie-break of
any kind.

Every header row and profile here is synthetic, authored for this test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finrecon.adapters.bank.schema import (
    BankProfileRegistry,
    MatchStatus,
    MatchTier,
    inspect_bank_csv,
    normalize_header,
    normalize_headers,
    read_signature,
    signature_from_headers,
)
from finrecon.adapters.bank.schema.detect import BuiltInProfileVerificationError, resolve_verified_built_in
from finrecon.adapters.bank.schema.registry import REGISTRY_ARTIFACT_VERSION
from finrecon.adapters.bank.schema.signature import BankSchemaReadError

HEADERS = ["Value Date", "Narration", "Debit", "Credit"]


def artifact(profile_id: str, headers=None, **profile_overrides) -> dict:
    profile = {
        "profile_id": profile_id,
        "currency": "INR",
        "value_date_column": "Value Date",
        "value_date_format": "%d/%m/%Y",
        "narration_column": "Narration",
        "money_columns": {
            "kind": "debit_credit",
            "debit_column": "Debit",
            "credit_column": "Credit",
        },
    }
    profile.update(profile_overrides)
    return {
        "registry_artifact_version": REGISTRY_ARTIFACT_VERSION,
        "profile_id": profile_id,
        "label": f"Synthetic {profile_id}",
        "version": "v1",
        "verification": "demo_fixture",
        "description": "Authored for tests only.",
        "evidence": "Synthetic; describes no real bank.",
        "expected_headers": list(headers if headers is not None else HEADERS),
        "profile": profile,
    }


def registry_of(tmp_path: Path, *artifacts: dict) -> BankProfileRegistry:
    for index, payload in enumerate(artifacts):
        (tmp_path / f"{index}.json").write_text(json.dumps(payload), encoding="utf-8")
    return BankProfileRegistry.from_directory(tmp_path)


def csv_bytes(header_line: str, encoding: str = "utf-8") -> bytes:
    return (header_line + "\n18/08/2026,NEFT,,1250.00\n").encode(encoding)


@pytest.fixture()
def single(tmp_path: Path) -> BankProfileRegistry:
    return registry_of(tmp_path, artifact("synthetic_a_v1"))


class TestNormalizationIsRepresentationOnly:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("﻿Value Date", "value date"),
            ("  Value Date  ", "value date"),
            ("Value    Date", "value date"),
            ("Value\tDate", "value date"),
            ("VALUE DATE", "value date"),
        ],
    )
    def test_it_folds_only_bom_whitespace_and_case(self, raw, expected):
        assert normalize_header(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["Value-Date", "Value.Date", "Val Date", "Date of Value", "ValueDate"],
    )
    def test_it_never_folds_punctuation_abbreviation_or_synonymy(self, raw):
        assert normalize_header(raw) != "value date"

    def test_it_preserves_column_order(self):
        assert normalize_headers(["B", "A"]) == ("b", "a")
        assert normalize_headers(["B", "A"]) != normalize_headers(["A", "B"])


class TestSignature:
    def test_it_carries_the_raw_headers_for_audit(self):
        signature = signature_from_headers(("﻿Value Date", " Debit "), delimiter=",", encoding="utf-8")
        assert signature.raw_headers == ("﻿Value Date", " Debit ")
        assert signature.normalized_headers == ("value date", "debit")
        assert signature.field_count == 2

    def test_the_digest_is_stable_and_value_independent(self):
        one = read_signature(csv_bytes(",".join(HEADERS)), delimiter=",", encoding="utf-8")
        other_rows = (",".join(HEADERS) + "\n01/01/2020,OTHER,900.00,\n").encode("utf-8")
        two = read_signature(other_rows, delimiter=",", encoding="utf-8")
        assert one.digest == two.digest

    def test_representation_differences_share_a_digest_but_reordering_does_not(self):
        base = signature_from_headers(tuple(HEADERS), delimiter=",", encoding="utf-8")
        spaced = signature_from_headers(("value  date", "NARRATION", " Debit", "Credit "), delimiter=",", encoding="utf-8")
        reordered = signature_from_headers(("Narration", "Value Date", "Debit", "Credit"), delimiter=",", encoding="utf-8")
        assert base.digest == spaced.digest
        assert base.digest != reordered.digest

    def test_a_different_delimiter_is_a_different_signature(self):
        comma = signature_from_headers(tuple(HEADERS), delimiter=",", encoding="utf-8")
        semi = signature_from_headers(tuple(HEADERS), delimiter=";", encoding="utf-8")
        assert comma.digest != semi.digest

    def test_utf8_and_utf8_sig_are_one_encoding_family(self):
        plain = signature_from_headers(tuple(HEADERS), delimiter=",", encoding="utf-8")
        sig = signature_from_headers(tuple(HEADERS), delimiter=",", encoding="utf-8-sig")
        assert plain.digest == sig.digest

    def test_undecodable_bytes_and_an_empty_file_are_read_errors(self):
        with pytest.raises(BankSchemaReadError):
            read_signature(b"\xff\xfe\x00bad", delimiter=",", encoding="utf-8")
        with pytest.raises(BankSchemaReadError, match="no header row"):
            read_signature(b"", delimiter=",", encoding="utf-8")


class TestTiersThatMayAutoSelect:
    def test_exact_headers_match_at_the_exact_tier(self, single):
        found = inspect_bank_csv(csv_bytes(",".join(HEADERS)), single)
        assert found.status is MatchStatus.MATCHED
        assert found.match_tier is MatchTier.EXACT
        assert found.profile.profile_id == "synthetic_a_v1"

    def test_a_case_only_difference_matches_as_safe_normalized(self, single):
        found = inspect_bank_csv(csv_bytes("VALUE DATE,narration,DEBIT,credit"), single)
        assert found.status is MatchStatus.MATCHED
        assert found.match_tier is MatchTier.SAFE_NORMALIZED

    def test_whitespace_differences_match_as_safe_normalized(self, single):
        found = inspect_bank_csv(csv_bytes(" Value Date ,Narration ,  Debit,Credit "), single)
        assert found.status is MatchStatus.MATCHED
        assert found.match_tier is MatchTier.SAFE_NORMALIZED

    def test_repeated_internal_whitespace_matches_as_safe_normalized(self, single):
        found = inspect_bank_csv(csv_bytes("Value   Date,Narration,Debit,Credit"), single)
        assert found.status is MatchStatus.MATCHED
        assert found.match_tier is MatchTier.SAFE_NORMALIZED

    def test_a_bom_on_the_first_header_is_not_a_false_mismatch(self, single):
        """A BOM is how Windows tooling writes UTF-8, not a schema change.

        The profile declares plain ``utf-8``, so the mark survives decoding
        and is folded away by normalization -- the file is recognised, at
        the safe-normalized tier rather than silently as exact.
        """
        found = inspect_bank_csv(csv_bytes(",".join(HEADERS), encoding="utf-8-sig"), single)
        assert found.status is MatchStatus.MATCHED
        assert found.match_tier is MatchTier.SAFE_NORMALIZED

    def test_an_exact_match_is_never_diluted_by_a_normalized_neighbour(self, tmp_path: Path):
        """Tiers are tried strongest-first and never mixed: an entry that
        would also match after normalization must not turn a clean exact
        match into an ambiguity."""
        registry = registry_of(
            tmp_path,
            artifact("synthetic_exact_v1"),
            artifact("synthetic_cased_v1", headers=["VALUE DATE", "NARRATION", "DEBIT", "CREDIT"],
                     value_date_column="VALUE DATE", narration_column="NARRATION",
                     money_columns={"kind": "debit_credit", "debit_column": "DEBIT", "credit_column": "CREDIT"}),
        )
        found = inspect_bank_csv(csv_bytes(",".join(HEADERS)), registry)
        assert found.status is MatchStatus.MATCHED
        assert found.match_tier is MatchTier.EXACT
        assert found.profile.profile_id == "synthetic_exact_v1"


class TestWhatMustNeverAutoSelect:
    @pytest.mark.parametrize(
        ("description", "header_line"),
        [
            ("reordered columns", "Narration,Value Date,Debit,Credit"),
            ("an extra column", "Value Date,Narration,Debit,Credit,Balance"),
            ("a missing column", "Value Date,Narration,Debit"),
            ("punctuation stripped", "ValueDate,Narration,Debit,Credit"),
            ("punctuation added", "Value.Date,Narration,Debit,Credit"),
            ("an abbreviation", "Val Dt,Narration,Debit,Credit"),
            ("a synonym", "Value Date,Description,Withdrawal,Deposit"),
            ("a one-character edit", "Value Dat,Narration,Debit,Credit"),
            ("materially different headers", "Txn Ref,Posted On,Particulars,Amount,Type"),
        ],
    )
    def test_it_does_not_auto_match(self, single, description, header_line):
        found = inspect_bank_csv(csv_bytes(header_line), single)
        assert found.status is MatchStatus.UNKNOWN, description
        assert found.profile is None
        assert found.match_tier is None

    def test_an_unknown_schema_names_no_nearest_candidate(self, single):
        found = inspect_bank_csv(csv_bytes("Value Date,Narration,Debit"), single)
        assert found.candidates == ()

    def test_the_observed_headers_are_still_reported_for_an_unknown_file(self, single):
        found = inspect_bank_csv(csv_bytes("Txn Ref,Posted On,Particulars"), single)
        assert found.observed.raw_headers == ("Txn Ref", "Posted On", "Particulars")
        assert found.observed.digest


class TestAmbiguityFailsClosed:
    @pytest.fixture()
    def tied(self, tmp_path: Path) -> BankProfileRegistry:
        """Two entries declaring the same header row -- a legitimate thing
        for two banks to do, and unresolvable without asking a human."""
        return registry_of(tmp_path, artifact("synthetic_a_v1"), artifact("synthetic_b_v2"))

    def test_two_exact_matches_are_ambiguous_and_select_nothing(self, tied):
        found = inspect_bank_csv(csv_bytes(",".join(HEADERS)), tied)
        assert found.status is MatchStatus.AMBIGUOUS
        assert found.profile is None
        assert {entry.profile_id for entry in found.candidates} == {"synthetic_a_v1", "synthetic_b_v2"}

    def test_two_normalized_matches_are_ambiguous_too(self, tied):
        found = inspect_bank_csv(csv_bytes("VALUE DATE,NARRATION,DEBIT,CREDIT"), tied)
        assert found.status is MatchStatus.AMBIGUOUS
        assert found.match_tier is MatchTier.SAFE_NORMALIZED
        assert len(found.candidates) == 2

    def test_no_tie_break_of_any_kind_resolves_it(self, tmp_path: Path):
        """Not newest, not highest version, not alphabetical, not first
        registered -- each would silently pick a mapping for the user."""
        newer = artifact("synthetic_z_v9")
        newer["version"] = "v9"
        registry = registry_of(tmp_path, artifact("synthetic_a_v1"), newer)
        found = inspect_bank_csv(csv_bytes(",".join(HEADERS)), registry)
        assert found.status is MatchStatus.AMBIGUOUS
        assert found.profile is None


class TestServerSideVerification:
    def test_a_matching_file_resolves_to_the_requested_profile(self, single):
        entry, found = resolve_verified_built_in(
            "synthetic_a_v1", csv_bytes(",".join(HEADERS)), single
        )
        assert entry.profile_id == "synthetic_a_v1"
        assert found.match_tier is MatchTier.EXACT

    def test_a_mismatching_file_is_refused(self, single):
        with pytest.raises(BuiltInProfileVerificationError) as caught:
            resolve_verified_built_in(
                "synthetic_a_v1", csv_bytes("Txn Ref,Posted On,Particulars"), single
            )
        assert caught.value.code == "bank_profile_mismatch"

    def test_an_unknown_profile_id_is_refused(self, single):
        with pytest.raises(BuiltInProfileVerificationError) as caught:
            resolve_verified_built_in("no_such_v1", csv_bytes(",".join(HEADERS)), single)
        assert caught.value.code == "unknown_built_in_profile"

    def test_an_ambiguous_file_cannot_be_resolved_by_the_client_picking_a_side(self, tmp_path: Path):
        """Selection, not mere membership, is the bar -- otherwise a client
        could settle an ambiguity the UI deliberately refuses to settle."""
        registry = registry_of(tmp_path, artifact("synthetic_a_v1"), artifact("synthetic_b_v2"))
        with pytest.raises(BuiltInProfileVerificationError) as caught:
            resolve_verified_built_in("synthetic_a_v1", csv_bytes(",".join(HEADERS)), registry)
        assert caught.value.code == "bank_profile_mismatch"
        assert "ambiguous" in str(caught.value)


class TestNoModelInvolvement:
    def test_the_detection_path_imports_nothing_from_the_agent_stack(self):
        """Deterministic recognition of reviewed schemas has no business
        holding a prompt, a provider, or a confidence score.

        Checked structurally rather than by grepping prose, so the modules
        stay free to *document* why none of that belongs here.
        """
        import ast

        from finrecon.adapters.bank.schema import detect, normalize, registry, signature

        for module in (detect, normalize, registry, signature):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            assert not [name for name in imported if "agent" in name], module.__name__
