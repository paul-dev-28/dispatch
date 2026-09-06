# Dispatch — hands-free field technician assistant

Dispatch is a full-duplex voice agent for field technicians. The project focuses on one hard voice problem: **interruption and recovery while an operational lookup is still running**. Perceived response time is measured as a secondary performance dimension.

## The hard voice problem

A technician may ask Dispatch to perform a slow field operation and then immediately change their mind. A correct voice agent must:

1. stop queued/playing speech when the user interrupts;
2. invalidate the old conversational generation;
3. cancel cancellable work or fence results from work that cannot be cancelled;
4. ensure the stale result never enters the current conversational response; and
5. answer the replacement request.

The deliberate demo case is:

> “Check the status of part 9911.”
>
> while that operation is running: “Actually, cancel that, tell me the weather at the site instead.”

The simulated backend uses a fixed delay (`SIMULATED_TOOL_DELAY_S`, default 4 seconds) so this behavior is reproducible.

## Architecture

```text
Browser / React + LiveKit
       │ microphone + WebRTC audio
       ▼
LiveKit room
       │
       ▼
Python LiveKit Agent
  Silero VAD → Deepgram STT → OpenAI LLM
                         │
                         ├── field tools / simulated backend
                         │
                         └── Rime TTS (WebSocket)
                               │
                               ▼
                         LiveKit audio track
                               │
                               ▼
                            Browser
```

The application-level safety mechanism is a monotonic **turn generation**. A tool captures the generation when it starts. An interruption invalidates that generation. A result whose generation is no longer current is discarded instead of being returned to the LLM.

## Rime configuration

Rime is the primary and default spoken output. The current configuration is controlled by:

- model: `RIME_MODEL_ID` (default `mistv2`)
- speaker: `RIME_SPEAKER` (default `cove`)
- language: `RIME_LANGUAGE` (default `eng`)
- transport: WebSocket when `RIME_USE_WEBSOCKET=true`
- audio format: `RIME_AUDIO_FORMAT` (default `pcm`)
- sample rate: `RIME_SAMPLE_RATE` (default `22050`)

LiveKit's current Rime plugin supports direct Rime API usage and WebSocket streaming; WebSocket mode is enabled explicitly here. Verify the selected speaker against the current Rime voice catalog before the demo. urlRime + LiveKit documentationhttps://docs.livekit.io/agents/models/tts/rime/ urlRime voice cataloghttps://docs.rime.ai/docs/voices

## Project layout

```text
agent/
  main.py              LiveKit agent entrypoint and metrics/interruption wiring
  pipeline.py          Dispatch agent and five deterministic field tools
  backend.py           simulated field backend
  interruption.py      generation/fencing guard
  provider_config.py   public provider configuration + env validation
  token_server.py      LiveKit token + provider + browser telemetry API
  logging_utils.py     JSONL event logger

client/
  src/App.jsx
  src/components/VoicePanel.jsx
  src/components/ProviderBadge.jsx
  src/components/StatusBoard.jsx
  src/components/PlaybackMetrics.jsx
  src/lib/playbackMetrics.js
  src/styles.css

eval/
  fixtures/             scripted utterances
  run_latency_test.py   verifies an operator-executed 20-turn live run
  analyze_latency.py    calculates p50/p95/min/max
  run_stress_test.py    deterministic application-level 10-trial acceptance test
  results/              generated evidence artifacts
```

## Setup

### 1. Python

Use Python 3.11+ and install the pinned dependencies:

```bash
cd agent
python -m pip install -r requirements.txt
cd ..
```

### 2. Node

```bash
cd client
npm install
cd ..
```

### 3. Environment

```bash
cp .env.example .env
cp client/.env.example client/.env
```

Fill the root `.env` with real LiveKit, Rime, Deepgram and OpenAI credentials. Secrets are used only by the backend/agent and are never sent to the browser.

## Run

Three terminals from the repository root:

```bash
python agent/token_server.py
python agent/main.py dev
cd client && npm run dev
```

Open the Vite URL, allow microphone access, and click **Enable audio** if the browser asks for permission.

The provider badge is fetched from the backend `/provider` endpoint, so it represents the configured backend provider rather than a browser-only copy of the settings.

## Reproducing the hard-problem test

### Application-level deterministic test

This test does not require cloud credentials. It proves the generation/fencing invariant:

```bash
python eval/run_stress_test.py --trials 10 --delay 0.01
```

The delay is intentionally short in CI-style execution. The live demo uses the configured 4-second delay.

A successful run produces a machine-readable artifact at:

```text
eval/results/stress_test.json
```

### Live interruption test

For the actual voice acceptance test:

1. Set `SIMULATED_TOOL_DELAY_S=4`.
2. Start the token server, agent and browser.
3. Say: `Check the status of part 9911.`
4. After the agent starts speaking or while the tool is still running, say: `Actually, cancel that, tell me the weather at the site instead.`
5. Confirm that the old speech stops, the old tool result is cancelled/fenced, and the final response is the weather response.
6. Repeat 10 times and preserve the JSONL log and browser telemetry.

The browser records `client_playback_started`, `client_playback_paused` and `client_playback_ended` using monotonic browser time plus a periodically synchronized server-clock estimate. This is intentionally separate from Rime TTFB: TTFB is a pipeline metric, not proof that audio was audible.

## Latency test

`eval/fixtures/latency_utterances.json` contains exactly 20 scripted utterances. Run them through a fresh browser session for the **cold** run and through an already-warm session for the **warm** run.

After an operator has performed each structured fixture utterance in a real browser session and populated `eval/results/events.jsonl` (once with a fresh session for cold, and once after warm-up):

```bash
python eval/run_latency_test.py \
  --events eval/results/events.jsonl \
  --label warm \
  --output eval/results/latency_warm.json
```

The verifier requires STT final, LLM TTFT, Rime TTFB and browser playback-start for every valid turn. The repository does **not** fabricate end-to-end numbers when the necessary live/browser timestamps are absent.

## Acceptance targets

These are **project-defined targets**, not official hackathon thresholds:

- perceived response time: p50 < 800 ms and p95 < 1500 ms for the complete user path, measured separately for cold and warm runs;
- interruption: zero stale responses across 10 repeated trials;
- interruption playback stop: target p95 < 150 ms after the detected interruption, measured from synchronized wall-clock telemetry.

If the measured system misses a target, report the miss. Do not edit the target or result to make the submission pass.

## Known limitations

- True cloud end-to-end latency requires the configured LiveKit/Rime/Deepgram/OpenAI services and a real browser/microphone. Those numbers cannot be generated honestly in an offline environment.
- The demo field backend is deterministic and simulated; replace `FieldBackend` methods with real APIs for production.
- Browser wall-clock synchronization is an estimate based on request/response midpoint timing; interruption-stop measurements should therefore be reported with that limitation.
- The single-room demo has no production authentication, authorization or tenant isolation.
- Rime WebSocket streaming and the chosen voice/model should be rechecked immediately before submission against the current Rime catalog.

## Security

Never commit `.env`, API keys, LiveKit secrets or browser-exposed credentials. The browser only receives a short-lived LiveKit access token and public provider metadata.

## Configuration

Runtime credentials and deployment/demo parameters are loaded from environment
variables. Copy `.env.example` to `.env` and provide your LiveKit, Rime,
Deepgram, and OpenAI credentials locally. Secrets are never embedded in source.

The centralized configuration includes provider settings, tool delays, demo
parameters, evaluation counts, server port, and allowed client origins.
