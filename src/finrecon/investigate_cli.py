"""Stage-3 diagnostic CLI: investigate a split's unresolved cases.

Operational facts only. It reports how many cases were investigated, how
many resolved, which blockers fired, how many steps the loop took and which
provider and model answered -- and it reports **no accuracy at all**, for the
same reason ``reconcile_cli`` does not: a production controller has no
ground truth, and accuracy belongs to the Stage-4 benchmark harness, which
does not exist yet. There is deliberately no ``make eval`` target.

Two modes:

``--replay-only``
    Zero provider calls. Every case must already have a cached trajectory or
    the run stops. This is the shape ``make eval`` will take in Stage 4.

live (default)
    Builds the provider chain from the environment and investigates cache
    misses. Start small: ``--limit`` exists so a first live run costs four
    cases rather than two hundred.

The provider configuration is printed before anything runs -- order, model
IDs, and which credentials are present -- because a run that quietly used a
fallback model is a run whose numbers mean something different. Credentials
themselves are never printed; only whether each one is set.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from finrecon.agent.cache import DEFAULT_FIXTURE_DIR, ReplayMissError, TrajectoryCache
from finrecon.agent.loop import DEFAULT_MAX_STEPS, MAX_TOOL_CALLS_PER_STEP, LoopConfig
from finrecon.agent.providers.base import ProviderConfigurationError
from finrecon.agent.providers.config import build_chain, describe_configuration
from finrecon.decide.config import DEFAULT_POLICY
from finrecon.ledger.store import open_ledger
from finrecon.loader import default_benchmark_dir
from finrecon.pipeline import process_batch
from finrecon.stage3 import run_stage3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m finrecon.investigate_cli",
        description="Investigate a split's unresolved cases (Stage 3). Reports no accuracy.",
    )
    parser.add_argument("--split", default="dev", help="dataset split (default: dev)")
    parser.add_argument(
        "--ledger", default=":memory:", help="SQLite path, or :memory: (default)"
    )
    parser.add_argument(
        "--fixtures",
        default=str(DEFAULT_FIXTURE_DIR),
        help=f"trajectory cache directory (default: {DEFAULT_FIXTURE_DIR})",
    )
    parser.add_argument(
        "--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="agent step budget"
    )
    parser.add_argument(
        "--max-tool-calls-per-step",
        type=int,
        default=MAX_TOOL_CALLS_PER_STEP,
        help="maximum tool calls admitted from one model turn",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="investigate at most N cases (by case ID order)"
    )
    parser.add_argument(
        "--case", action="append", default=None, help="investigate only this case ID (repeatable)"
    )
    parser.add_argument(
        "--replay-only",
        action="store_true",
        help="serve every case from cache; make zero provider calls",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help=(
            "provider identity for the cache key in --replay-only mode "
            "(default: first in the configured order). A trajectory produced "
            "by one model is not interchangeable with one produced by another, "
            "so replay still has to say which it wants."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model identity for the cache key in --replay-only mode",
    )
    parser.add_argument(
        "--show-trajectory",
        action="store_true",
        help="print the full trajectory of each investigated case",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    benchmark_dir = default_benchmark_dir()

    configuration = describe_configuration()
    print("provider configuration:")
    print(json.dumps(configuration, indent=2, sort_keys=True))

    chain = None
    provider_id = model = None
    if args.replay_only:
        # Replay needs the provider *identity* for the cache key, but never
        # a provider object -- there is nothing here that could make a call,
        # and no credential is read. The identity comes from the configured
        # order's head unless it is given explicitly.
        head = configuration["providers"][0]
        provider_id = args.provider or head["provider"]
        model = args.model or head["model"]
        print(
            f"\nmode: REPLAY-ONLY (zero provider calls)"
            f"\n      cache identity: {provider_id}:{model}"
        )
    else:
        try:
            chain = build_chain()
        except ProviderConfigurationError as error:
            # Loud, not silent. A "run" that escalated every case for want of
            # a key would look like a result, and someone would report it.
            print(f"\ncannot start a live run: {error}")
            print("  Set a credential (see .env.example), or use --replay-only.")
            return 2
        provider_id = chain.providers[0].provider_id
        model = chain.providers[0].model
        print(f"\nmode: LIVE via chain {list(chain.describe())}")

    with open_ledger(args.ledger) as store:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=args.split)
        snapshots = sorted(batch.snapshots, key=lambda s: s.case_id)
        selected = [s.case_id for s in snapshots]
        if args.case:
            selected = [c for c in selected if c in set(args.case)]
        if args.limit is not None:
            selected = selected[: args.limit]

        print(
            f"\nsplit={args.split}  cases={len(batch.decisions)}  "
            f"stage-2 unresolved={len(snapshots)}  investigating={len(selected)}"
        )

        try:
            result = run_stage3(
                store=store,
                batch_result=batch,
                chain=chain,
                cache=TrajectoryCache(Path(args.fixtures)),
                config=LoopConfig(
                    max_steps=args.max_steps,
                    max_tool_calls_per_step=args.max_tool_calls_per_step,
                ),
                policy=DEFAULT_POLICY,
                replay_only=args.replay_only,
                provider_id=provider_id,
                model=model,
                case_ids=frozenset(selected),
            )
        except ReplayMissError as miss:
            # A miss is the correct outcome, not a crash: replay must never
            # quietly reach for a provider to fill a gap in the corpus.
            print(f"\nreplay miss: {miss}")
            print(
                f"  fixtures dir: {args.fixtures}\n"
                f"  cache identity: {provider_id}:{model}\n"
                "  Run without --replay-only (with a credential configured) to "
                "record trajectories first, or point --fixtures at a warmed corpus."
            )
            return 2

        print(f"\ninvestigated: {len(result.outcomes)}")
        print(f"  cache hits:  {result.cache_hits()}")
        print(f"  resolved:    {len(result.resolved())}")
        print(f"  escalated:   {len(result.escalated())}")

        if result.outcomes:
            steps = [o.trajectory.step_count for o in result.outcomes]
            print(f"  steps mean:  {sum(steps) / len(steps):.2f}  max: {max(steps)}")
            terminations = Counter(o.trajectory.termination_reason for o in result.outcomes)
            print(f"  termination: {dict(sorted(terminations.items()))}")
            models = Counter(m for o in result.outcomes for m in o.trajectory.models_used)
            print(f"  models requested: {dict(sorted(models.items()))}")
            reported = Counter(
                m for o in result.outcomes for m in o.trajectory.models_reported
            )
            # Printed separately, not merged: a gateway that resolved an alias
            # ran a different model than the one asked for, and a run whose
            # numbers came from a substituted model means something else.
            print(f"  models answered:  {dict(sorted(reported.items())) or 'not reported'}")
            fallbacks = Counter(
                r for o in result.outcomes for r in o.trajectory.fallback_reasons
            )
            print(f"  fallbacks:   {dict(sorted(fallbacks.items())) or 'none'}")
            tokens = [
                o.trajectory.total_tokens()
                for o in result.outcomes
                if o.trajectory.total_tokens() is not None
            ]
            if tokens:
                print(f"  tokens:      total {sum(tokens)}  mean {sum(tokens) / len(tokens):.0f}")

        blockers = result.blocker_counts()
        if blockers:
            print("\nescalation blockers:")
            for name, count in sorted(blockers.items()):
                print(f"  {name:<40} {count}")

        if args.show_trajectory:
            for outcome in result.outcomes:
                print("\n" + "=" * 72)
                print(json.dumps(outcome.trajectory.model_dump(mode="json"), indent=2))

        print(
            "\nNo accuracy is reported here. A production controller has no ground "
            "truth; accuracy belongs to the Stage-4 benchmark harness."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
