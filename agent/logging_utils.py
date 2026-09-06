import json
import os
import time
from pathlib import Path

RESULTS_DIR = Path(os.environ.get("EVAL_RESULTS_DIR", "./eval/results"))
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
_LOG_PATH = RESULTS_DIR / "events.jsonl"


def log_event(turn_id: str, event: str, **fields):
    """Write server events with a real epoch timestamp and correlation IDs."""
    record = {"turn_id": turn_id, "event": event, "timestamp": time.time(), **fields}
    # `ts` is retained for backwards-compatible analysis of existing logs.
    record["ts"] = record["timestamp"]
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record
