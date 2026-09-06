#!/usr/bin/env python3
"""Verify an operator-executed cold or warm 20-utterance live run.

It deliberately does not synthesize microphone input; the browser/LiveKit path
is the system under test. It rejects turns missing STT final, LLM TTFT, Rime
TTFB, or browser playback evidence.
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="eval/results/events.jsonl")
    parser.add_argument("--utterances", default="eval/fixtures/latency_utterances.json")
    parser.add_argument("--label", default="warm")
    parser.add_argument("--output", default="eval/results/latency_summary.json")
    args = parser.parse_args()

    utterances = json.loads(Path(args.utterances).read_text())
    lines = [json.loads(x) for x in Path(args.events).read_text().splitlines() if x.strip()]
    turns = {}
    for event in lines:
        if event.get("turn_id"):
            turns.setdefault(event["turn_id"], set()).add(event.get("event"))
    print(f"fixture_utterances: {len(utterances)}")
    complete = [tid for tid, events in turns.items() if {"stt_finalize", "llm_first_token", "rime_first_byte", "client_playback_started"} <= events]
    print(f"captured_turns: {len(turns)}")
    print(f"complete_turns: {len(complete)}")
    if len(utterances) != 20:
        raise SystemExit("Expected exactly 20 latency fixture utterances.")
    if len(complete) < 20:
        raise SystemExit(f"Only {len(complete)}/20 complete turns. Keep the partial result; no success count was fabricated.")

    cmd = [sys.executable, str(Path(__file__).with_name("analyze_latency.py")), "--events", args.events, "--label", args.label, "--output", args.output]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
