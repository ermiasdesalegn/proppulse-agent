"""Command line for the evaluations.

    python -m evals                      run everything from the recording (free, instant) and write evals/REPORT.md
    python -m evals --check              the same, and exit with an error if anything got worse (used by CI)
    python -m evals retrieval            only the retrieval evaluation
    python -m evals --mode record        ask the real model for anything not yet recorded (uses Gemini quota; safe to run again)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from evals import retrieval
from evals.harness import HERE, RECORDING, close_env, open_env

RESULTS = HERE / "results.json"
SUITES = ("retrieval", "scenarios")


async def run(args: argparse.Namespace) -> dict:
    out: dict = {"mode": args.mode, "ran_at": datetime.now(UTC).isoformat(timespec="seconds")}
    if "retrieval" in args.suites:
        cases = retrieval.load_cases()
        env = await open_env(args.mode)
        try:
            results = await retrieval.run_all(env, cases, progress=lambda m: print(m, flush=True) if args.mode == "record" else None)
            out["retrieval"] = {"summary": retrieval.summarize(results), "cases": retrieval.to_json(results), "misses": retrieval.misses(results)}
        finally:
            live = env.live_calls
            await close_env(env)
        out["retrieval"]["live_calls"] = live
        sweep = []
        for threshold in retrieval.THRESHOLD_SWEEP:
            env = await open_env(args.mode, similarity_threshold=threshold)
            try:
                rs = await retrieval.run_all(env, cases)
            finally:
                await close_env(env)
            s = retrieval.summarize(rs)
            sweep.append({"threshold": threshold, "hit_at_3": s["overall"]["hit_at_3"], "precision_at_3": s["overall"]["precision_at_3"],
                          "false_positives": s["no_match_expected"]["false_positives"], "correctly_empty": s["no_match_expected"]["correctly_empty"]})
        out["retrieval"]["threshold_sweep"] = sweep
    if "scenarios" in args.suites:
        from evals import scenarios

        out["scenarios"] = await scenarios.run_all(args.mode, only=args.only)
    return out


def main() -> int:
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suites", nargs="*", help="which suites to run (default: all): " + ", ".join(SUITES))
    parser.add_argument("--mode", choices=("replay", "record"), default="replay")
    parser.add_argument("--only", help="run only scenarios whose name contains this text")
    parser.add_argument("--check", action="store_true", help="exit with an error if results are below evals/thresholds.json")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="show the agent's own error logs (handled failures are expected in some scenarios)")
    args = parser.parse_args()
    if not args.verbose:
        logging.getLogger("app").setLevel(logging.CRITICAL)  # the agent logs the failures it handles; an evaluation reports them as results instead
    args.suites = args.suites or list(SUITES)
    if unknown := [x for x in args.suites if x not in SUITES]:
        parser.error(f"unknown suite {unknown[0]!r}; choose from {', '.join(SUITES)}")
    if args.mode == "replay" and not Path(RECORDING).exists():
        print(f"No recording at {RECORDING}. Run `python -m evals --mode record` once (it uses Gemini quota).")
        return 2
    results = asyncio.run(run(args))
    RESULTS.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    from evals import report

    if not args.no_report:
        report.write(results)
    print(report.console(results))
    return report.check(results) if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
