#!/usr/bin/env python3
"""Deterministic acceptance test for interruption/fencing.

This tests the application-level invariant without requiring LiveKit/Rime credentials:
slow work is started, the turn is invalidated, the slow result is fenced, and the
replacement request completes. It is not an end-to-end audio test.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from backend import FieldBackend
from interruption import TurnGuard


async def one_trial(delay_s: float) -> dict:
    guard = TurnGuard()
    first_generation, first_turn = guard.new_turn()
    backend = FieldBackend(delay_s=delay_s)
    started = time.perf_counter()

    stale_result = None

    async def slow_lookup():
        nonlocal stale_result
        try:
            stale_result = await backend.lookup_part_status("9911")
        except asyncio.CancelledError:
            return "cancelled"
        if guard.fence(first_generation):
            return "stale_discarded"
        return stale_result

    task = asyncio.create_task(slow_lookup())
    await asyncio.sleep(min(0.05, delay_s / 4))
    second_generation, second_turn = guard.invalidate()
    interruption_time = time.perf_counter()

    result_state = await task
    if result_state != "stale_discarded":
        return {"passed": False, "reason": f"slow result was {result_state}"}

    weather = await backend.get_site_weather()
    elapsed_ms = (time.perf_counter() - started) * 1000

    return {
        "passed": weather["condition"] == "clear" and second_generation > first_generation,
        "first_generation": first_generation,
        "second_generation": second_generation,
        "stale_result_discarded": True,
        "replacement_completed": True,
        "replacement_response": f"{weather['temperature_c']} degrees Celsius and {weather['condition']}",
        "interruption_to_fence_ms": (interruption_time - started) * 1000,
        "elapsed_ms": elapsed_ms,
        "first_turn_id": first_turn,
        "second_turn_id": second_turn,
    }


async def run(trials: int, delay_s: float):
    results = [await one_trial(delay_s) for _ in range(trials)]
    passed = sum(r["passed"] for r in results)
    return {"trials": trials, "passed": passed, "failed": trials - passed, "success_rate": passed / trials, "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--delay", type=float, default=4.0)
    parser.add_argument("--output", default="eval/results/stress_test.json")
    args = parser.parse_args()
    report = asyncio.run(run(args.trials, args.delay))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(f"trials: {report['trials']}")
    print(f"passed: {report['passed']}")
    print(f"failed: {report['failed']}")
    print(f"success_rate: {report['success_rate']:.0%}")
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
