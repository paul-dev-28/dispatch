#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    x = (len(values) - 1) * p / 100
    lo, hi = math.floor(x), math.ceil(x)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (x - lo)


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", default="eval/results/live_stress_summary.json")
    args = parser.parse_args()
    events = sorted(load(args.events), key=lambda e: e.get("ts", e.get("browser_ts", 0)))

    interruptions = [e for e in events if e.get("event") == "barge_in_detected"]
    pauses = [e for e in events if e.get("event") in {"client_playback_paused", "client_playback_ended"} and e.get("browser_ts") is not None]
    stop_latencies = []
    for interruption in interruptions:
        t = interruption.get("ts")
        following = [p for p in pauses if p["browser_ts"] >= t and p["browser_ts"] - t <= 3]
        if following:
            stop_latencies.append((following[0]["browser_ts"] - t) * 1000)

    stale_count = sum(e.get("event") == "stale_result_discarded" for e in events)
    recovery_count = sum(e.get("event") == "recovery_success" for e in events)
    result = {
        "interruption_events": len(interruptions),
        "stale_results_discarded": stale_count,
        "recovery_success_events": recovery_count,
        "interruption_to_client_stop_ms": {
            "n": len(stop_latencies),
            "p50": percentile(stop_latencies, 50),
            "p95": percentile(stop_latencies, 95),
        },
        "note": "Pairing uses synchronized epoch timestamps. Client pause/ended events are playback telemetry; inspect individual trials before claiming a stop was caused by the interruption.",
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
