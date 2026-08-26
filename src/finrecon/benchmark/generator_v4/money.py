"""Exact integer-paise break-up composition for v4 cases.

Every v4 candidate in a case settles to the **same** net, because that is
what makes amount blocking useless as a discriminator and forces the case to
turn on evidence instead. But identical nets reached by identical arithmetic
would make the candidates clones, and a clone set is a weaker test than a
realistic one: real same-amount contention arises from *different* gross
amounts landing on the same net after different deductions.

So each candidate here is given its own gross and its own mix of declared
break-up lines -- fee, GST on fee, an optional processed refund, an optional
sub-account transfer -- and the gross is then solved for exactly, in
integers, so the lines total the shared net to the paise.

No float enters this module. ``net_for_gross`` is the same integer floor
arithmetic as
:func:`finrecon.benchmark.generator.record_factory.fee_breakup`, imported
rather than restated so the two cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass

from finrecon.benchmark.generator.record_factory import fee_breakup

FEE_BPS = 190
GST_BPS = 1800


class BreakupConstructionError(RuntimeError):
    """No gross amount produces the requested net under the declared rates."""


def net_for_gross(gross_paise: int) -> int:
    _fee, _gst, net = fee_breakup(gross_paise, fee_bps=FEE_BPS, gst_bps=GST_BPS)
    return net


def gross_for_net(target_net_paise: int) -> int:
    """The smallest gross whose fee/GST arithmetic lands exactly on ``target_net_paise``.

    ``net_for_gross`` is non-decreasing and rises by zero or one paise per
    paise of gross, so it takes every integer value in its range and a
    bisection always terminates on an exact hit. The exactness is asserted
    rather than assumed: a miss would mean the declared rates changed, and
    silently returning a near-miss would put an unexplained paise into a
    generated case.
    """
    if target_net_paise <= 0:
        raise BreakupConstructionError(f"target net must be positive; got {target_net_paise}")

    low, high = target_net_paise, target_net_paise * 2 + 1_000
    while low < high:
        middle = (low + high) // 2
        if net_for_gross(middle) < target_net_paise:
            low = middle + 1
        else:
            high = middle
    if net_for_gross(low) != target_net_paise:
        raise BreakupConstructionError(
            f"no gross amount yields a net of exactly {target_net_paise} paise under "
            f"fee={FEE_BPS}bps gst={GST_BPS}bps"
        )
    return low


@dataclass(frozen=True)
class BreakupPlan:
    """One settlement's arithmetic, solved and checked before any record exists."""

    net_paise: int
    gross_paise: int
    fee_paise: int
    gst_paise: int
    refund_paise: int
    """Positive magnitude of a ``refund`` line, or ``0`` for no refund line."""
    transfer_paise: int
    """Positive magnitude of a ``transfer`` deduction, or ``0`` for no transfer line."""

    def __post_init__(self) -> None:
        total = (
            self.gross_paise
            - self.fee_paise
            - self.gst_paise
            - self.refund_paise
            - self.transfer_paise
        )
        if total != self.net_paise:
            raise BreakupConstructionError(
                f"break-up totals {total} paise but the settlement is {self.net_paise}; "
                "a generated case may never carry an unexplained paise"
            )


def plan_breakup(
    *,
    net_paise: int,
    refund_paise: int = 0,
    transfer_paise: int = 0,
) -> BreakupPlan:
    """Solve for the gross that makes these deductions total ``net_paise`` exactly."""
    if refund_paise < 0 or transfer_paise < 0:
        raise BreakupConstructionError("deduction magnitudes are stated positive")
    gross = gross_for_net(net_paise + refund_paise + transfer_paise)
    fee, gst, _ = fee_breakup(gross, fee_bps=FEE_BPS, gst_bps=GST_BPS)
    return BreakupPlan(
        net_paise=net_paise,
        gross_paise=gross,
        fee_paise=fee,
        gst_paise=gst,
        refund_paise=refund_paise,
        transfer_paise=transfer_paise,
    )


def no_zero_amount_paise(rng) -> int:
    """A four-digit paise amount whose decimal digits contain no zero.

    Used for the amounts a v4 narration names out loud. The no-zero rule is
    the same one the references follow, and for the same reason: a digit run
    with no zero in it cannot be a suffix of a zero-padded record identifier,
    so an amount field can never accidentally stand in a
    ``suffix_of_reference`` relation to exactly one settlement ID.
    """
    digits = "".join(rng.choice("123456789") for _ in range(4))
    return int(digits)


__all__ = [
    "FEE_BPS",
    "GST_BPS",
    "BreakupConstructionError",
    "BreakupPlan",
    "gross_for_net",
    "net_for_gross",
    "no_zero_amount_paise",
    "plan_breakup",
]
