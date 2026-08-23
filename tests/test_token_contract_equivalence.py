"""The benchmark's token contract and the production tokenizer must stay equivalent.

``finrecon.benchmark.generator.token_contract`` is a *deliberate*
reimplementation of ``finrecon.normalize.tokens``. Benchmark v3 exists
because the generator's T0 admission test was weaker than the rule the
matcher applies, and the fix was to restate the matcher's contract on the
benchmark side rather than to import it — an assertion that imports the
code it checks restates that code and proves nothing.

That independence is worth keeping and it has a cost: nothing failed when
the two drifted, because nothing compared them. The v3 adversarial review
verified the equivalence by hand and recorded the absence of an automated
guard as the one gap worth closing. This module is that guard.

**Only the test imports both sides.** ``token_contract`` must not import
``finrecon.normalize.tokens``, and the assertions below are what makes the
duplication safe instead of merely duplicated.

Three layers, because cross-comparison alone is not enough:

1. **Differential** — for every corpus input, the two implementations must
   return the same tokens and the same fold.
2. **Anchored** — a subset of inputs is also pinned to hard-coded expected
   values. Cross-comparison cannot catch a change applied to *both* sides;
   an anchor can.
3. **End-to-end** — the benchmark's ``is_usable_direct_key`` is compared
   against what the real ``DirectKeyIndex`` actually reaches, through
   ``normalize_settlement``/``normalize_bank_record`` and
   ``match_direct_key``. Nothing about the production key path is
   re-implemented here.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from finrecon.benchmark.generator import token_contract as bench
from finrecon.matchers.direct_key_matcher import DirectKeyIndex, match_direct_key
from finrecon.models import BankRecord, BankRecordDirection, Settlement, SettlementLineItem
from finrecon.models.money import Paise
from finrecon.models.settlement import SettlementLineType
from finrecon.normalize.records import (
    normalize_bank_record,
    normalize_batch,
    normalize_settlement,
)
from finrecon.normalize.tokens import token_key, tokenize_narration

# --------------------------------------------------------------------------
# Adversarial corpus
# --------------------------------------------------------------------------

CANONICAL_IDENTIFIERS = [
    "setl_dev_000001",
    "setl_frozeneval_000001",
    "setl_frozen-eval_000001",
    "pay_dev_000001",
    "ORD-dev-000001",
    "rfnd_frozeneval_000060",
    "bnk_frozeneval_000890",
]
"""Real generated identifier shapes, including the one v3 was created to fix."""

DELIMITER_PROBES = [
    "ABC123",
    "ABC-123",
    "ABC/123",
    "ABC_123",
    "ABC 123",
    "ABC*123",
    "ABC:123",
    "ABC.123",
    "ABC+123",
    "ABC,123",
    "ABC|123",
    "ABC#123",
    "ABC(123)",
]
"""One separator at a time. ``_`` is the only one that must stay inside a token."""

REPEATED_AND_MIXED_DELIMITERS = [
    "A--B",
    "A//B",
    "A___B",
    "A - B",
    "A -- B",
    "A/-/B",
    "A__-__B",
    "A_-_B",
    "-A-",
    "/A/",
    "__A__",
    "A_B-C/D E",
    "leading-token",
    "token-trailing",
    "-leading-delimiter",
    "trailing-delimiter-",
    "prefixABC123suffix",
    "prefix-ABC123-suffix",
    "prefix_ABC123_suffix",
]

EMPTY_AND_WHITESPACE = [
    "",
    " ",
    "   ",
    "\t",
    "\t\t",
    "\n",
    "\r\n",
    " \t \n ",
    "A  B",
    "A   \t  B",
    " A ",
    "\tA\n",
    "A\nB",
    "A\rB",
    "A\tB",
]

BARE_DELIMITERS = ["*", ":", ".", "/", "-", "+", "|", "#", "%", "@", "!", "?", "~", "^", "&"]

BARE_TOKEN_CHARS = ["A", "a", "0", "9", "_", "__", "Z9_"]

NON_ASCII = [
    "ÅBC123",          # ÅBC123 — Latin-1 supplement, outside A-Za-z
    "åbc123",          # åbc123
    "ΑΒΓ",   # ΑΒΓ — Greek
    "АБВ",   # АБВ — Cyrillic
    "ABC 123",         # non-breaking space as separator
    " ",               # lone non-breaking space
    "ABCß123",         # ß — .upper() maps to 'SS', changing length
    "ß",
    "İı",         # İ ı — dotted/dotless i, casing edge cases
    "ABC–123",         # en-dash, not ASCII hyphen
    "中文",         # CJK
    "café-123",
    "\U0001f600",           # emoji (astral plane)
]

NARRATION_SHAPES = [
    "RZPY/SETL/setl_dev_000001 CREDIT",
    "RZPY/SETL/setl_frozen-eval_000001 CREDIT",
    "NEFT CR-RZRPAY-SET98372-MUM",
    "RZPY*ORD293 UPI/98273192",
    "RZPY/SETL/8172 REF:PAY88/REV",
    "CR NEFT-RZPY-STLMNT/29X17/REV-8271",
    "NEFT CREDIT - SETTLEMENT",
    "IMPS CREDIT RECEIVED",
    "BANK CREDIT - ONLINE TXN SETTLEMENT",
    "RTGS CR - VENDOR PAYOUT",
    "NEFT CR - PAYMENT GATEWAY SETTLEMENT",
    "  RZPY   SETL   setl_dev_000042  ",
    "setl_dev_000042",
    "setl_dev_000042suffix",
    "prefixsetl_dev_000042",
    "XX/setl_dev_000042/XX",
    "setl_dev_000042 setl_dev_000043",
]

TOKENIZATION_CORPUS = (
    CANONICAL_IDENTIFIERS
    + DELIMITER_PROBES
    + REPEATED_AND_MIXED_DELIMITERS
    + EMPTY_AND_WHITESPACE
    + BARE_DELIMITERS
    + BARE_TOKEN_CHARS
    + NON_ASCII
    + NARRATION_SHAPES
)


def _label(value: str) -> str:
    return repr(value)


# --------------------------------------------------------------------------
# Layer 1 — differential equivalence
# --------------------------------------------------------------------------


class TestTokenizationEquivalence:
    """Same input, same tokens, on both sides of the intentional duplication."""

    @pytest.mark.parametrize("value", TOKENIZATION_CORPUS, ids=_label)
    def test_narration_tokenization_agrees(self, value):
        assert bench.narration_tokens(value) == tokenize_narration(value), (
            "token_contract and normalize.tokens disagree on tokenization; the benchmark's "
            "T0 admission test no longer states the rule the matcher applies"
        )

    def test_the_corpus_is_actually_large_and_covers_both_outcomes(self):
        """A differential test over a corpus that never splits anything proves nothing."""
        assert len(TOKENIZATION_CORPUS) >= 100
        assert any(len(bench.narration_tokens(v)) == 0 for v in TOKENIZATION_CORPUS)
        assert any(len(bench.narration_tokens(v)) == 1 for v in TOKENIZATION_CORPUS)
        assert any(len(bench.narration_tokens(v)) >= 3 for v in TOKENIZATION_CORPUS)


class TestFoldEquivalence:
    """``token_contract.fold`` and ``normalize.tokens.token_key`` are one rule."""

    @pytest.mark.parametrize("value", TOKENIZATION_CORPUS, ids=_label)
    def test_folding_agrees(self, value):
        assert bench.fold(value) == token_key(value)

    @pytest.mark.parametrize("value", TOKENIZATION_CORPUS, ids=_label)
    def test_folding_every_token_agrees(self, value):
        """Fold is applied per token in production, so check it there too."""
        produced = tuple(token_key(t) for t in tokenize_narration(value))
        assert tuple(bench.fold(t) for t in bench.narration_tokens(value)) == produced

    def test_folded_token_set_matches_the_production_token_keys(self):
        """``folded_token_set`` must equal the set of production ``reference_token_keys``."""
        for value in TOKENIZATION_CORPUS:
            expected = {token_key(t) for t in tokenize_narration(value)}
            assert bench.folded_token_set(value) == expected, value


# --------------------------------------------------------------------------
# Layer 2 — anchors against a shared drift
# --------------------------------------------------------------------------

ANCHORED_TOKENIZATIONS = [
    ("setl_dev_000001", ("setl_dev_000001",)),
    ("setl_frozeneval_000001", ("setl_frozeneval_000001",)),
    ("setl_frozen-eval_000001", ("setl_frozen", "eval_000001")),
    ("ORD-dev-000001", ("ORD", "dev", "000001")),
    ("ABC123", ("ABC123",)),
    ("ABC-123", ("ABC", "123")),
    ("ABC/123", ("ABC", "123")),
    ("ABC_123", ("ABC_123",)),
    ("ABC 123", ("ABC", "123")),
    ("A--B", ("A", "B")),
    ("A//B", ("A", "B")),
    ("A___B", ("A___B",)),
    ("leading-token", ("leading", "token")),
    ("token-trailing", ("token", "trailing")),
    ("prefixABC123suffix", ("prefixABC123suffix",)),
    ("", ()),
    ("   ", ()),
    ("\t", ()),
    ("\n", ()),
    ("*", ()),
    (":", ()),
    (".", ()),
    ("/", ()),
    ("-", ()),
    ("_", ("_",)),
    ("ÅBC123", ("BC123",)),
    ("ΑΒΓ", ()),
    ("ABC 123", ("ABC", "123")),
    ("RZPY/SETL/setl_dev_000001 CREDIT", ("RZPY", "SETL", "setl_dev_000001", "CREDIT")),
    (
        "RZPY/SETL/setl_frozen-eval_000001 CREDIT",
        ("RZPY", "SETL", "setl_frozen", "eval_000001", "CREDIT"),
    ),
]


class TestAnchoredSemantics:
    """Pinned expectations, so a change applied to *both* sides still fails.

    A purely differential suite is blind to a coordinated edit — widening
    the delimiter class in both modules at once would keep every equivalence
    assertion green. These anchors are the independent statement of the
    contract that such an edit has to argue with.
    """

    @pytest.mark.parametrize("value,expected", ANCHORED_TOKENIZATIONS, ids=lambda v: repr(v))
    def test_tokenization_matches_the_pinned_contract(self, value, expected):
        assert bench.narration_tokens(value) == expected
        assert tokenize_narration(value) == expected

    def test_the_delimiter_class_is_exactly_the_declared_one(self):
        assert bench.TOKEN_DELIMITERS.pattern == r"[^A-Za-z0-9_]+"

    def test_underscore_is_the_only_punctuation_kept_inside_a_token(self):
        for char in BARE_DELIMITERS:
            assert tokenize_narration(f"A{char}B") == ("A", "B"), char
            assert bench.narration_tokens(f"A{char}B") == ("A", "B"), char
        assert tokenize_narration("A_B") == ("A_B",)
        assert bench.narration_tokens("A_B") == ("A_B",)

    def test_folding_is_upper_casing_and_nothing_else(self):
        for value in ("abc", "ABC", "aBc123", "åbc", "ß"):
            assert bench.fold(value) == value.upper()
            assert token_key(value) == value.upper()


# --------------------------------------------------------------------------
# Layer 3 — end-to-end against the real direct-key matcher
# --------------------------------------------------------------------------

_BASE = datetime(2026, 4, 1, 10, 0, 0)
_VALUE_DATE = date(2026, 4, 1)
_AMOUNT = 100_000


def _settlement(settlement_id: str, *, utr: str | None) -> Settlement:
    return Settlement(
        settlement_id=settlement_id,
        utr=utr,
        amount=Paise(_AMOUNT),
        created_at=_BASE,
        breakup=(
            SettlementLineItem(
                type=SettlementLineType.PAYMENT, amount=Paise(_AMOUNT), reference_id="pay_probe_1"
            ),
        ),
    )


def _bank_record(narration: str) -> BankRecord:
    return BankRecord(
        bank_record_id="bnk_probe_000001",
        amount=Paise(_AMOUNT),
        direction=BankRecordDirection.CREDIT,
        narration=narration,
        value_date=_VALUE_DATE,
    )


def _production_reaches(narration: str, identifier: str, *, as_utr: bool) -> bool:
    """Does the real ``DirectKeyIndex`` reach ``identifier`` from ``narration``?

    Runs the genuine production path — ``normalize_settlement``,
    ``normalize_bank_record``, ``DirectKeyIndex``, ``match_direct_key`` —
    and reads the reference evidence the matcher itself recorded. Nothing
    about tokenization or key derivation is restated here, which is what
    makes this a check on production behaviour rather than a second copy
    of it.

    "Reached" is deliberately reference evidence, not a RESOLVED status: a
    reached settlement can still be refused for an amount delta, and that
    refusal is a different rule from "no candidate".
    """
    settlement = (
        _settlement("setl_probe_000001", utr=identifier)
        if as_utr
        else _settlement(identifier, utr=None)
    )
    bank_record = _bank_record(narration)
    batch = normalize_batch(
        orders=[], payments=[], refunds=[], settlements=[settlement], bank_records=[bank_record]
    )
    index = DirectKeyIndex(batch.settlements)
    decision = match_direct_key(
        normalize_bank_record(bank_record), batch, index, "case:probe"
    )
    return bool(decision.evidence.references)


# (narration, identifier) pairs. Every one is a shape the benchmark relies on
# being classified correctly, plus the near-misses that must NOT be reachable.
REACHABILITY_PAIRS = [
    # complete token
    ("RZPY/SETL/setl_dev_000042 CREDIT", "setl_dev_000042", True),
    ("setl_dev_000042", "setl_dev_000042", True),
    ("setl_dev_000042 CREDIT", "setl_dev_000042", True),
    ("CREDIT setl_dev_000042", "setl_dev_000042", True),
    # surrounded by delimiters
    ("XX/setl_dev_000042/XX", "setl_dev_000042", True),
    ("XX-setl_dev_000042-XX", "setl_dev_000042", True),
    ("XX setl_dev_000042 XX", "setl_dev_000042", True),
    ("XX*setl_dev_000042:XX", "setl_dev_000042", True),
    # joined to a prefix or suffix — glued, so not a whole token
    ("prefixsetl_dev_000042", "setl_dev_000042", False),
    ("setl_dev_000042suffix", "setl_dev_000042", False),
    ("Xsetl_dev_000042X", "setl_dev_000042", False),
    # underscore does not split, so an underscore-glued id is one longer token
    ("setl_dev_000042_EXTRA", "setl_dev_000042", False),
    ("EXTRA_setl_dev_000042", "setl_dev_000042", False),
    # split by a hyphen — the exact v3 defect
    ("RZPY/SETL/setl_frozen-eval_000042 CREDIT", "setl_frozen-eval_000042", False),
    ("setl_frozen-eval_000042", "setl_frozen-eval_000042", False),
    # ...while its token-safe replacement is reachable
    ("RZPY/SETL/setl_frozeneval_000042 CREDIT", "setl_frozeneval_000042", True),
    # split by slash / whitespace
    ("AB/CD", "AB/CD", False),
    ("AB CD", "AB CD", False),
    ("AB-CD", "AB-CD", False),
    ("NEFT-CR-AB12-CD34", "AB12-CD34", False),
    # ...but the fragments themselves are whole tokens
    ("NEFT-CR-AB12-CD34", "AB12", True),
    ("NEFT-CR-AB12-CD34", "CD34", True),
    ("AB/CD", "AB", True),
    ("AB CD", "CD", True),
    # case folding
    ("ref abc123 cr", "ABC123", True),
    ("REF ABC123 CR", "abc123", True),
    ("ref AbC123 cr", "aBc123", True),
    # absent
    ("NEFT CREDIT - SETTLEMENT", "setl_dev_000042", False),
    ("", "setl_dev_000042", False),
    ("   ", "setl_dev_000042", False),
    # non-ASCII
    ("REF ÅBC123 CR", "BC123", True),
    ("REF ABC 123 CR", "ABC", True),
    ("REF ABC–123 CR", "ABC", True),
]


class TestDirectKeyUsabilityEquivalence:
    """``is_usable_direct_key`` must agree with what the matcher actually reaches."""

    @pytest.mark.parametrize(
        "narration,identifier,expected",
        REACHABILITY_PAIRS,
        ids=lambda v: repr(v) if isinstance(v, str) else str(v),
    )
    def test_settlement_id_reachability_agrees(self, narration, identifier, expected):
        benchmark_says = bench.is_usable_direct_key(narration, identifier)
        production_says = _production_reaches(narration, identifier, as_utr=False)
        assert benchmark_says == production_says, (
            f"benchmark={benchmark_says} production={production_says} for "
            f"narration={narration!r} settlement_id={identifier!r}"
        )
        assert benchmark_says == expected, "pinned expectation disagrees with both implementations"

    @pytest.mark.parametrize(
        "narration,identifier,expected",
        REACHABILITY_PAIRS,
        ids=lambda v: repr(v) if isinstance(v, str) else str(v),
    )
    def test_utr_reachability_agrees(self, narration, identifier, expected):
        """The UTR arm of the index, which is a separate code path from settlement_id."""
        benchmark_says = bench.is_usable_direct_key(narration, identifier)
        production_says = _production_reaches(narration, identifier, as_utr=True)
        assert benchmark_says == production_says, (
            f"benchmark={benchmark_says} production={production_says} for "
            f"narration={narration!r} utr={identifier!r}"
        )
        assert benchmark_says == expected

    def test_an_empty_or_absent_identifier_is_never_usable_on_either_side(self):
        assert bench.is_usable_direct_key("NEFT CREDIT", None) is False
        assert bench.is_usable_direct_key("NEFT CREDIT", "") is False
        # Production drops an empty UTR key rather than indexing it.
        assert _production_reaches("NEFT CREDIT", "", as_utr=True) is False

    def test_token_safety_predicts_reachability_from_a_narration_that_embeds_the_id(self):
        """``is_token_safe`` is the generator's gate; it must predict matcher reach."""
        for identifier in CANONICAL_IDENTIFIERS:
            narration = f"RZPY/SETL/{identifier} CREDIT"
            assert bench.is_token_safe(identifier) == _production_reaches(
                narration, identifier, as_utr=False
            ), identifier


# --------------------------------------------------------------------------
# The one real divergence, pinned rather than papered over
# --------------------------------------------------------------------------


class TestKnownUtrStripAsymmetry:
    """Production strips a UTR before folding it; the benchmark does not.

    ``normalize_settlement`` derives ``utr_key`` as
    ``token_key(settlement.utr.strip())``, so a UTR carrying surrounding
    whitespace is indexed under its stripped key and *is* reachable.
    ``is_usable_direct_key`` folds the identifier as given, so it says no.

    This is the only divergence between the two implementations, and it is
    recorded here for three reasons rather than removed:

    * It is **unreachable** in generated data — asserted below against the
      committed artifacts.
    * It fails **safe**. The benchmark is the stricter side, so a case that
      hit this would fail generation with ``TierDisjointnessError`` rather
      than be emitted as a mislabelled T0.
    * Removing it means editing production normalization, which is out of
      scope for a test-only change and would alter Stage-2 behaviour.

    If a future change makes whitespace-bearing UTRs possible, this test is
    where the consequence is already written down.
    """

    PADDED = " ABC123 "

    def test_the_two_sides_diverge_only_for_a_whitespace_padded_utr(self):
        narration = "REF ABC123 CR"
        assert bench.is_usable_direct_key(narration, self.PADDED) is False
        assert _production_reaches(narration, self.PADDED, as_utr=True) is True

    def test_the_benchmark_is_the_stricter_side(self):
        """Direction matters: stricter means "fails generation", not "ships mislabelled"."""
        narration = "REF ABC123 CR"
        assert bench.is_usable_direct_key(narration, self.PADDED) <= _production_reaches(
            narration, self.PADDED, as_utr=True
        )

    def test_the_settlement_id_arm_has_no_such_asymmetry(self):
        """``settlement_id_key`` is not stripped, so both sides agree there."""
        narration = "REF ABC123 CR"
        assert bench.is_usable_direct_key(narration, self.PADDED) is False
        assert _production_reaches(narration, self.PADDED, as_utr=False) is False

    def test_no_committed_utr_carries_whitespace_so_the_divergence_is_unreachable(
        self, benchmark_dir
    ):
        import json

        for split in ("dev", "frozen-eval"):
            path = benchmark_dir / "datasets" / split / "settlements.jsonl"
            utrs = [
                record["utr"]
                for record in (
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line
                )
                if record.get("utr")
            ]
            assert utrs, f"{split} has no UTRs to check"
            assert all(utr == utr.strip() for utr in utrs), split
            assert all(bench.is_token_safe(utr) for utr in utrs), split


# --------------------------------------------------------------------------
# The independence the duplication exists to protect
# --------------------------------------------------------------------------


class TestTheDuplicationStaysIndependent:
    """The benchmark side must not collapse into an import of the production side.

    Making this test pass by importing ``finrecon.normalize.tokens`` into
    ``token_contract`` would make every assertion above tautological. The
    guard is asserted structurally so that shortcut is closed.
    """

    def test_token_contract_does_not_import_the_production_tokenizer(self):
        import ast
        from pathlib import Path

        source = Path(bench.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module and "normalize" in module:
                pytest.fail(
                    f"token_contract imports {module!r}; the benchmark's statement of the "
                    "contract must stay independent of the production tokenizer"
                )

    def test_the_two_modules_define_the_delimiter_class_separately(self):
        from pathlib import Path

        from finrecon.normalize import tokens as prod

        bench_source = Path(bench.__file__).read_text(encoding="utf-8")
        prod_source = Path(prod.__file__).read_text(encoding="utf-8")
        pattern = r"[^A-Za-z0-9_]+"
        assert pattern in bench_source
        assert pattern in prod_source
