# RIME_EVIDENCE.md

## Submission status

This file deliberately distinguishes **locally verified application behavior** from **cloud/browser measurements that require the submitter's credentials and a real microphone/browser**. No performance number is fabricated.

## Hard voice claim

Dispatch targets **interruption and recovery during a long-running field operation**, with perceived response time as a secondary measured property.

The core invariant is:

```text
old request starts
      ↓
user interrupts
      ↓
turn generation increments
      ↓
old speech is interrupted by LiveKit
      ↓
old cancellable work is cancelled, or uncancellable work is fenced
      ↓
stale result cannot enter the current response
      ↓
new request completes and is spoken
```

The pinned `livekit-agents==1.8.0` package exposes `overlapping_speech`; Dispatch advances a generation only when that event has `is_interruption=True`. `SpeechHandle.interrupted` records cancellation, and cancellable function tools opt into `ToolFlag.CANCELLABLE`.

## Claim A — perceived response time

### Definition

For a live run, the desired end-to-end measurement is:

```text
end of user's utterance
        →
first browser playback of the Rime response
```

The implementation separately records:

- STT/EOU delay;
- LLM TTFT;
- Rime TTS TTFB;
- browser playback-start telemetry.

Rime WebSocket mode is enabled because LiveKit documents it as a lower-latency streaming path. urlLiveKit Rime TTS documentationhttps://docs.livekit.io/agents/models/tts/rime/

### Project-defined target

- cold p50 < 800 ms
- cold p95 < 1500 ms
- warm p50 < 800 ms
- warm p95 < 1500 ms

These are project acceptance targets, **not official hackathon thresholds**.

### Required live procedure

1. Start a fresh browser/LiveKit session.
2. Run all 20 utterances in `eval/fixtures/latency_utterances.json`.
3. Preserve `eval/results/events.jsonl`.
4. Repeat after the session is warm.
5. Run `eval/run_latency_test.py` for each result set.
6. Copy only measured values into this file.

### Current repository verification

**Cloud/browser measurement status: NOT RUN in this offline environment.**

Reason: true STT → LLM → Rime → WebRTC → browser playback requires live service credentials and a real browser/microphone. The repository therefore leaves the live result fields unfilled rather than inventing values.

### Result table to complete after the live run

| Metric | Cold p50 | Cold p95 | Warm p50 | Warm p95 |
|---|---:|---:|---:|---:|
| EOU | pending live run | pending live run | pending live run | pending live run |
| LLM TTFT | pending live run | pending live run | pending live run | pending live run |
| Rime TTFB | pending live run | pending live run | pending live run | pending live run |
| Browser playback start | pending live run | pending live run | pending live run | pending live run |
| End-to-end | pending live run | pending live run | pending live run | pending live run |

## Claim B — interruption and recovery

### Definition

When the technician interrupts a long-running operation, the agent must:

1. stop queued/playing speech;
2. invalidate the old generation;
3. cancel cancellable work or fence an uncancellable late result;
4. never speak the stale result as current; and
5. complete the replacement request.

### Acceptance target

- 10/10 live trials with zero stale responses;
- project target p95 interruption-to-local-playback-stop < 150 ms;
- zero stale tool results entering the current conversational response.

### Deterministic application-level verification

The repository contains `eval/run_stress_test.py`. It exercises the same generation/fencing invariant without depending on cloud services.

The latest local verification performed while preparing this repository was:

```text
trials: 10
passed: 10
failed: 0
success_rate: 100%
```

This is **not** an end-to-end voice/audio result. It verifies only the deterministic application-level stale-result invariant.

Artifact:

```text
eval/results/stress_test.json
```

### Live procedure

1. Configure `SIMULATED_TOOL_DELAY_S=4`.
2. Start the real agent and browser.
3. Say `Check the status of part 9911.`
4. Interrupt with `Actually, cancel that, tell me the weather at the site instead.`
5. Repeat 10 times.
6. Verify the browser playback-stop telemetry and JSONL event sequence.
7. Confirm the final spoken answer is the weather response and that no part-status response is spoken after the interruption.

### Required live evidence

The final submission should include the actual 10-trial result generated from the live run. Until that run is performed, the repository must not claim a live 100% success rate.

## Exact Rime path

The judged path uses the Rime plugin directly:

```text
Rime API → livekit-plugins-rime → LiveKit agent → WebRTC audio track → browser playback
```

Current configuration defaults are:

```text
model:       mistv2
speaker:     cove
language:    eng
transport:   WebSocket
format:      pcm
sample rate: 22050 Hz
```

The selected Rime model/voice combination should be checked against the current Rime voice catalog immediately before the final demo. Rime's current documentation lists Mist v2 as an available model and provides a machine-readable voice catalog. urlRime modelshttps://docs.rime.ai/docs/models urlRime voiceshttps://docs.rime.ai/api-reference/data/voices-v2

## Limitations

- The application-level 10/10 result does not prove network, STT, LLM, Rime or browser performance.
- Live interruption detection depends on the configured LiveKit turn/interruption handling and microphone conditions.
- Browser playback-stop timing includes the limits of browser event timing and the clock-offset estimate.
- The field tools are deterministic simulations, not production APIs.
- No claim should be made about p50/p95 end-to-end voice latency until the 20-turn cold/warm browser tests have actually been run.
