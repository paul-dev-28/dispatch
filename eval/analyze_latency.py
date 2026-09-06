#!/usr/bin/env python3
"""Analyze only correlated, fully observed live turns; never invent missing values."""
import argparse, json, math
from collections import defaultdict
from pathlib import Path

def percentile(v, p):
    if not v: return None
    v = sorted(v); x = (len(v)-1)*p/100; lo, hi = math.floor(x), math.ceil(x)
    return v[lo] if lo == hi else v[lo] + (v[hi]-v[lo])*(x-lo)

def summary(v):
    return {"count":len(v), "min_ms":min(v) if v else None, "p50_ms":percentile(v,50), "p95_ms":percentile(v,95), "max_ms":max(v) if v else None}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--events",required=True); p.add_argument("--label",default="run"); p.add_argument("--output"); a=p.parse_args()
    turns=defaultdict(list)
    for line in Path(a.events).read_text().splitlines():
        if line.strip():
            e=json.loads(line); turns[e.get("turn_id","unknown")].append(e)
    values={k:[] for k in ("eou_ms","llm_ttft_ms","rime_ttfb_ms","client_playback_latency_ms","end_to_end_ms")}
    complete=0
    for event_list in turns.values():
        by_name={e["event"]:e for e in event_list}
        eou, llm, rime, play=(by_name.get(x) for x in ("stt_finalize","llm_first_token","rime_first_byte","client_playback_started"))
        if eou: values["eou_ms"].append(eou["end_of_utterance_delay"]*1000)
        if llm: values["llm_ttft_ms"].append(llm["ttft"]*1000)
        if rime: values["rime_ttfb_ms"].append(rime["ttfb"]*1000)
        # Browser monotonic time is only comparable within this browser turn.
        if eou and play and eou.get("browser_performance_ms") is not None:
            values["end_to_end_ms"].append(play["browser_performance_ms"]-eou["browser_performance_ms"])
        if all((eou,llm,rime,play)): complete += 1
    result={"label":a.label,"observed_turns":len(turns),"complete_turns":complete,"metrics":{k:summary(v) for k,v in values.items()},"pending": "Any metric with count 0 was not observed; it is not a zero-latency result."}
    print(json.dumps(result,indent=2))
    if a.output: Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(result,indent=2))
if __name__=="__main__": main()
