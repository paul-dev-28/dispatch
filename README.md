# Dispatch - Hands Free Field Technician Voice Assistant

Dispatch is a full-duplex, hands-free voice assistant designed for field
technicians. It combines real-time speech recognition, Gemini reasoning,
streaming Rime speech synthesis, LiveKit audio transport, and
generation-based interruption control.

The central engineering problem is **safe interruption and recovery
while an operational task is still running**.

A technician can start a slow operation and immediately change their
mind:

> "Check the status of part 9911."

while the operation is still running:

> "Actually, cancel that, tell me the weather at the site instead."

Dispatch must stop the obsolete response, invalidate the old
conversational generation, prevent stale work from reaching the current
response, and answer the replacement request.

------------------------------------------------------------------------

## Demo Interface

![Dispatch web application](screenshots/web-app.png)

The web application provides:

-   Live microphone input through LiveKit/WebRTC.
-   Real-time user and assistant transcripts.
-   Current assistant state: Connecting, Listening, Thinking, or
    Speaking.
-   Microphone mute/unmute control.
-   Provider and voice configuration visibility.
-   Turn-level latency information.
-   Interruption and playback status.
-   Browser-side playback telemetry.

------------------------------------------------------------------------

## System Architecture

![Dispatch architecture](screenshots/architecture.jpeg)

The runtime pipeline is:

``` text
Field Technician
       │
       ▼
React + Vite Frontend
       │
       │ microphone / WebRTC audio
       ▼
LiveKit Room
       │
       ▼
Python LiveKit Agent
       │
       ├── Silero VAD
       │
       ├── Deepgram STT
       │
       ├── Gemini LLM
       │       │
       │       └── Field tools / simulated backend
       │
       └── Rime TTS
               │
               ▼
        LiveKit Audio Track
               │
               ▼
            Browser
```

The token server is responsible for issuing short-lived LiveKit access
tokens. Provider credentials remain server-side.

------------------------------------------------------------------------

# 1. Core Problem

Traditional voice assistants often handle a conversation as a simple
sequence:

``` text
User speaks
   ↓
Speech-to-text
   ↓
LLM
   ↓
Text-to-speech
   ↓
Assistant speaks
```

That model breaks down when the user interrupts the assistant while an
operation is still executing.

For example:

``` text
User:
"Check the status of part 9911."

        ↓

Slow field operation starts

        ↓

User:
"Actually, cancel that and tell me the weather at the site."

        ↓

Old operation may still finish
```

The dangerous behavior is allowing the old operation to produce a
response after the user has already moved on.

Dispatch therefore treats every conversational turn as belonging to a
monotonically increasing **generation**.

------------------------------------------------------------------------

# 2. Generation-Based Interruption Model

Each active conversational turn receives a generation number.

For example:

``` text
Generation 10
    ↓
"Check the status of part 9911."
    ↓
Slow operation starts

User interrupts

    ↓

Generation 10 becomes stale
    ↓
Generation 11 created
    ↓
"Tell me the weather at the site."
```

Any asynchronous operation that started under generation 10 can no
longer publish a result into generation 11.

The important invariant is:

``` text
Only the current generation may produce the current response.
```

This protects the system even when a backend operation cannot be
physically cancelled.

------------------------------------------------------------------------

# 3. Interruption Flow

Dispatch uses LiveKit's native interruption handling together with the
application's generation guard.

The intended sequence is:

``` text
User starts speaking
        ↓
Silero VAD detects speech
        ↓
LiveKit detects overlapping speech
        ↓
Current generation is invalidated
        ↓
Queued output audio is cleared
        ↓
Current assistant speech is forcefully interrupted
        ↓
Old LLM stream becomes stale
        ↓
Old TTS stream becomes stale
        ↓
New user turn is finalized
        ↓
New generation is created
        ↓
Gemini processes the replacement request
        ↓
Rime generates the new response
        ↓
LiveKit plays only the new response
```

The system therefore separates two concepts:

1.  **Physical cancellation** --- stop speech and clear queued audio
    whenever possible.
2.  **Logical cancellation** --- invalidate the old generation so stale
    asynchronous results can never become the active response.

This distinction is important for real-world systems because not every
external operation can be cancelled immediately.

------------------------------------------------------------------------

# 4. Current AI Pipeline

The current project uses:

  Component         Technology        Responsibility
  ----------------- ----------------- ----------------------------------------------
  Audio transport   LiveKit           Real-time bidirectional audio
  VAD               Silero            Detect speech and silence
  STT               Deepgram Nova-2   Convert user speech to text
  LLM               Gemini            Generate the assistant response
  TTS               Rime              Convert generated text into streaming speech
  Frontend          React + Vite      Voice UI, transcript and controls
  Token server      Python            Secure LiveKit token generation
  Backend           Python            Agent runtime and simulated field operations

The current LLM is **Gemini**, replacing the previous Groq
configuration.

The primary spoken output remains **Rime over WebSocket**.

The audio architecture has intentionally not been replaced with a native
Gemini audio/realtime pipeline.

------------------------------------------------------------------------

# 5. Rime Configuration

Rime is the primary speech synthesis provider.

The configuration is environment-driven:

``` text
RIME_MODEL_ID
RIME_SPEAKER
RIME_LANGUAGE
RIME_USE_WEBSOCKET
RIME_AUDIO_FORMAT
RIME_SAMPLE_RATE
```

Typical configuration:

``` text
model: mistv2
speaker: cove
language: eng
transport: WebSocket
audio format: pcm
sample rate: 22050
```

The Rime WebSocket transport is used for streaming speech generation.

LiveKit TTS-aligned transcription is enabled so the spoken output and
transcript can remain synchronized:

``` python
use_tts_aligned_transcript=True
```

The LiveKit room output also uses synchronized transcription:

``` python
room_io.RoomOptions(
    text_output=room_io.TextOutputOptions(
        sync_transcription=True,
    ),
)
```

------------------------------------------------------------------------

# 6. Current Interruption Configuration

The agent uses LiveKit's interruption system with VAD-based detection.

The current configuration is:

``` python
"interruption": {
    "enabled": True,
    "mode": "vad",
    "min_duration": 0.2,
    "min_words": 1,
    "false_interruption_timeout": None,
    "resume_false_interruption": False,
    "discard_audio_if_uninterruptible": True,
    "backchannel_boundary": (
        0.25,
        0.25,
    ),
}
```

The important behavior is that old speech is never deliberately resumed
after an actual interruption.

When overlapping speech is classified as a real interruption, the agent:

``` python
guard.invalidate()
```

then clears queued audio and requests:

``` python
await session.interrupt(force=True)
```

------------------------------------------------------------------------

# 7. LLM Stale-Generation Protection

The LLM stream captures the generation at the beginning of generation.

Conceptually:

``` python
speech_generation = guard.snapshot()
```

Every incoming LLM chunk is then checked:

``` python
if guard.is_stale(speech_generation):
    return
```

If the user has interrupted the assistant, the generation is no longer
current and the old stream terminates.

This prevents an old Gemini response from continuing to feed the
downstream speech pipeline.

------------------------------------------------------------------------

# 8. TTS Stale-Generation Protection

Rime TTS uses the same generation fence.

The TTS stream captures:

``` python
speech_generation = guard.snapshot()
```

Each audio frame is checked against the current generation.

If the generation becomes stale:

``` text
Old TTS generation
       ↓
Generation mismatch
       ↓
Stop streaming frames
       ↓
Clear queued audio
       ↓
Return
```

This prevents stale speech from continuing after a new conversational
turn has started.

------------------------------------------------------------------------

# 9. Conversation State

The application maintains the active conversation through the LiveKit
AgentSession and ChatContext.

The system is intentionally not a scripted demo.

Gemini receives the real conversation context and can answer:

-   General knowledge questions
-   Engineering questions
-   Programming questions
-   Mathematics questions
-   Science questions
-   Troubleshooting questions
-   Everyday questions
-   Field-operation requests
-   Follow-up questions using previous conversation context

The assistant is instructed not to invent previous messages, fake
measurements, fake tool results, or nonexistent conversations.

------------------------------------------------------------------------

# 10. Voice Interaction States

The frontend exposes the current LiveKit voice-agent state:

``` text
Connecting
Listening
Thinking
Speaking
```

The state is represented visually in the voice panel.

Example:

``` text
Listening
Ready to hear you
```

or:

``` text
Speaking
Playing the response
```

The microphone control allows the technician to mute or unmute the local
microphone without changing the underlying LiveKit audio architecture.

------------------------------------------------------------------------

# 11. Frontend Transcript Handling

The frontend consumes LiveKit's transcription text stream:

``` text
lk.transcription
```

Each transcription segment is associated with identifiers such as:

``` text
lk.segment_id
lk.transcribed_track_id
lk.transcription_final
```

The UI distinguishes between:

``` text
You
AI
```

and updates the same transcript segment as interim transcription becomes
final.

This allows the interface to show a live conversational transcript
without replacing the LiveKit audio pipeline.

------------------------------------------------------------------------

# 12. Playback Telemetry

The browser records playback lifecycle events separately from
server-side TTS metrics.

Relevant browser events include:

``` text
client_playback_started
client_playback_stopped
client_playback_ended
```

Server-side metrics include:

``` text
stt_finalize
llm_first_token
rime_first_byte
speech_created
speech_cancelled
speech_stopped
barge_in_detected
audio_buffer_cleared
```

This distinction is important.

Rime TTFB measures when TTS begins producing audio.

Browser playback-start measures when the browser actually begins playing
an audio track.

These are different measurements and should not be treated as the same
event.

------------------------------------------------------------------------

# 13. Latency Measurement

Dispatch records latency at multiple stages:

``` text
User speech
    ↓
STT finalization
    ↓
LLM first token
    ↓
Rime first byte
    ↓
Browser playback start
```

The frontend also synchronizes its clock against the backend so that
browser playback timestamps can be compared with server-side events.

The latency dashboard exposes values such as:

``` text
STT
LLM
TTS
Total
```

The total value should only be reported when the required timestamps are
available.

The project does not fabricate end-to-end latency numbers when browser
playback evidence is missing.

------------------------------------------------------------------------

# 14. Field Operation Demo

The deliberate demo scenario uses a deterministic simulated backend.

Example:

``` text
User:
"Check the status of part 9911."

        ↓

Field backend operation starts

        ↓

User:
"Actually, cancel that, tell me the weather at the site instead."

        ↓

Original generation becomes stale

        ↓

Old result is discarded

        ↓

New request is processed

        ↓

Dispatch answers the weather request
```

The simulated backend delay is configurable:

``` text
SIMULATED_TOOL_DELAY_S
```

The default live-demo delay is:

``` text
4 seconds
```

The delay makes interruption and stale-result fencing reproducible.

------------------------------------------------------------------------

# 15. Project Structure

``` text
dispatch/
│
├── agent/
│   ├── main.py
│   ├── pipeline.py
│   ├── backend.py
│   ├── interruption.py
│   ├── provider_config.py
│   ├── token_server.py
│   └── logging_utils.py
│
├── client/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── VoicePanel.jsx
│   │   │   ├── ProviderBadge.jsx
│   │   │   ├── StatusBoard.jsx
│   │   │   └── PlaybackMetrics.jsx
│   │   │
│   │   ├── lib/
│   │   │   └── playbackMetrics.js
│   │   │
│   │   └── styles.css
│   │
│   ├── package.json
│   └── ...
│
├── eval/
│   ├── fixtures/
│   ├── run_latency_test.py
│   ├── analyze_latency.py
│   ├── run_stress_test.py
│   └── results/
│
├── docs/
│   └── images/
│       ├── dispatch-web-app.png
│       └── dispatch-architecture.png
│
├── .env.example
├── README.md
└── ...
```

------------------------------------------------------------------------

# 16. Where to Put the Screenshots

Create this directory in the project:

``` text
docs/images/
```

Then put the two PNG files there with these exact names:

``` text
docs/images/dispatch-web-app.png
docs/images/dispatch-architecture.png
```

Your project should therefore contain:

``` text
dispatch/
└── docs/
    └── images/
        ├── dispatch-web-app.png
        └── dispatch-architecture.png
```

The README image references are relative paths:

``` markdown
![Dispatch web application](docs/images/dispatch-web-app.png)

![Dispatch architecture](docs/images/dispatch-architecture.png)
```

This means the images will render correctly on GitHub as long as the
README is located at the repository root.

------------------------------------------------------------------------

# 17. Installation

## Python Environment

Use Python 3.11 or newer.

Create and activate a virtual environment:

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the Python dependencies:

``` bash
pip install -r agent/requirements.txt
```

The environment must include the LiveKit Agents Google plugin because
Gemini is the active LLM provider.

If it is not already included in the requirements:

``` bash
pip install "livekit-agents[google]"
```

## Frontend

Install the React dependencies:

``` bash
cd client
npm install
cd ..
```

------------------------------------------------------------------------

# 18. Environment Configuration

Copy the example configuration:

``` bash
cp .env.example .env
cp client/.env.example client/.env
```

The backend `.env` contains provider credentials.

Typical variables include:

``` text
LIVEKIT_URL
LIVEKIT_API_KEY
LIVEKIT_API_SECRET

RIME_API_KEY
RIME_MODEL_ID
RIME_SPEAKER
RIME_LANGUAGE
RIME_USE_WEBSOCKET
RIME_AUDIO_FORMAT
RIME_SAMPLE_RATE

DEEPGRAM_API_KEY

GOOGLE_API_KEY

SIMULATED_TOOL_DELAY_S
```

Secrets must remain server-side.

Do not commit:

``` text
.env
client/.env
API keys
LiveKit API secrets
Rime API keys
Deepgram API keys
Gemini API keys
```

------------------------------------------------------------------------

# 19. Running Dispatch

Start the token server:

``` bash
python agent/token_server.py
```

In another terminal, start the LiveKit agent:

``` bash
python agent/main.py dev
```

In a third terminal, start the frontend:

``` bash
cd client
npm run dev
```

Open the Vite development URL in a browser.

Allow microphone access when requested.

If the browser asks to enable audio, enable it before beginning the
voice interaction.

------------------------------------------------------------------------

# 20. Basic Voice Test

After starting all services:

``` text
1. Open the web application.
2. Allow microphone access.
3. Confirm the status changes to Listening.
4. Ask a normal question.
5. Confirm the transcript appears.
6. Confirm Gemini generates the response.
7. Confirm Rime produces speech.
8. Confirm the browser plays the response.
```

Example:

``` text
"What is mathematics?"
```

The expected path is:

``` text
Microphone
    ↓
Deepgram
    ↓
Gemini
    ↓
Rime
    ↓
LiveKit
    ↓
Browser
```

------------------------------------------------------------------------

# 21. Interruption Acceptance Test

Set:

``` text
SIMULATED_TOOL_DELAY_S=4
```

Start the services.

Then say:

``` text
"Check the status of part 9911."
```

While the operation is running, interrupt with:

``` text
"Actually, cancel that, tell me the weather at the site instead."
```

Verify:

``` text
1. Existing assistant speech stops.
2. Queued old audio is cleared.
3. The old generation is invalidated.
4. The old operation is cancelled when possible.
5. Otherwise its result is fenced.
6. The stale result is not inserted into the current response.
7. The replacement request is processed.
8. Dispatch answers the replacement request.
```

Repeat the test multiple times.

------------------------------------------------------------------------

# 22. Deterministic Stress Test

The application-level stress test does not require live cloud
credentials.

Run:

``` bash
python eval/run_stress_test.py --trials 10 --delay 0.01
```

The test verifies the generation/fencing invariant.

The generated artifact is:

``` text
eval/results/stress_test.json
```

The deterministic test is complementary to the live voice test.

It does not replace real browser/microphone testing.

------------------------------------------------------------------------

# 23. Live Latency Test

The latency fixture contains 20 scripted utterances.

Run the structured live test using a fresh browser session for the cold
run and an already-warm session for the warm run.

After collecting live events:

``` bash
python eval/run_latency_test.py \
  --events eval/results/events.jsonl \
  --label warm \
  --output eval/results/latency_warm.json
```

The verifier expects the required stages for each valid turn:

``` text
STT final
LLM TTFT
Rime TTFB
Browser playback start
```

Do not manufacture missing timestamps.

------------------------------------------------------------------------

# 24. Performance Targets

These are project-defined acceptance targets, not official LiveKit,
Gemini, Deepgram, or Rime requirements.

Target perceived response time:

``` text
p50 < 800 ms
p95 < 1500 ms
```

Target interruption behavior:

``` text
0 stale responses across 10 repeated trials
```

Target interruption playback stop:

``` text
p95 < 150 ms after detected interruption
```

If the system misses a target, the result should be reported honestly
rather than modifying the target or evaluation artifact.

------------------------------------------------------------------------

# 25. Important Metrics

The system logs events such as:

``` text
session_started
turn_started
turn_cutoff
barge_in_detected
audio_buffer_cleared
audio_buffer_cleared_on_barge_in

speech_created
speech_cancelled
speech_stopped

llm_generation_started
llm_first_token
stale_llm_chunk_dropped

tts_generation_started
rime_first_byte
stale_tts_stream_terminated

stt_finalize

client_playback_started
client_playback_stopped
client_playback_ended
```

These events provide evidence for debugging:

``` text
interruption
stale responses
LLM latency
TTS latency
browser playback latency
```

------------------------------------------------------------------------

# 26. Conversation Safety Invariant

The central correctness rule is:

``` text
A result is valid only if its generation is still current.
```

For an operation that starts under generation `G`:

``` text
operation_generation = G
```

If the user interrupts:

``` text
current_generation = G + 1
```

When the old operation completes:

``` text
operation_generation != current_generation
```

Therefore:

``` text
DISCARD RESULT
```

This prevents an asynchronous operation from contaminating the new
conversation.

------------------------------------------------------------------------

# 27. Why Generation Fencing Matters

Physical cancellation alone is insufficient.

A network request may already be in progress.

A remote API may not support cancellation.

A database operation may already have completed.

A background coroutine may finish after an interruption.

Generation fencing provides a second layer of protection.

The architecture therefore follows:

``` text
Cancel when possible
        +
Fence when cancellation is impossible
        =
Safe conversational recovery
```

------------------------------------------------------------------------

# 28. Current Provider Architecture

The current provider configuration is:

``` text
STT:
Deepgram Nova-2

LLM:
Gemini

TTS:
Rime mistv2 / configured Rime model

Audio transport:
LiveKit

VAD:
Silero
```

The provider badge in the frontend is retrieved from the backend
`/provider` endpoint so that the displayed provider represents backend
configuration rather than a browser-only setting.

------------------------------------------------------------------------

# 29. Security

Provider credentials are never intentionally exposed to the browser.

The browser receives:

``` text
Short-lived LiveKit access token
```

and public/non-secret provider metadata.

The following must remain server-side:

``` text
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
RIME_API_KEY
DEEPGRAM_API_KEY
GOOGLE_API_KEY
```

Never commit these values to Git.

------------------------------------------------------------------------

# 30. Production Limitations

The current project is a demonstration and evaluation system rather than
a production multi-tenant platform.

Known limitations include:

-   The simulated field backend must be replaced by real operational
    APIs for production.
-   The demo room does not provide production-grade tenant isolation.
-   Authentication and authorization need to be strengthened for
    deployment.
-   Browser playback timing depends on synchronized client/server
    clocks.
-   Browser playback telemetry is not identical to Rime TTFB.
-   Cloud end-to-end latency requires a live browser, microphone,
    LiveKit connection, STT, LLM, and TTS services.
-   Rime model and speaker availability should be verified against the
    current Rime catalog before a final demo.
-   Gemini API quotas and rate limits depend on the Google AI project
    and billing tier.

------------------------------------------------------------------------

# 31. Troubleshooting

## No Gemini response

Check the agent terminal.

A Gemini quota error looks like:

``` text
429 Too Many Requests
RESOURCE_EXHAUSTED
```

This indicates an API quota/rate-limit problem rather than a LiveKit
audio problem.

Check the Gemini API usage and rate limits for the configured Google
project.

## Gemini model unavailable

If the configured model returns:

``` text
404 NOT_FOUND
```

verify that the model is currently available to the API project and
supported by the installed LiveKit Google plugin.

## No audio

Check:

``` text
RIME_API_KEY
RIME_MODEL_ID
RIME_SPEAKER
RIME_LANGUAGE
RIME_USE_WEBSOCKET
```

Then inspect the agent logs for:

``` text
rime_first_byte
speech_created
speech_cancelled
speech_stopped
```

## Transcript appears but audio is delayed

Compare:

``` text
rime_first_byte
```

with:

``` text
client_playback_started
```

A TTS first-byte event does not prove that the browser has started
audible playback.

## Old speech continues after interruption

Inspect:

``` text
barge_in_detected
audio_buffer_cleared_on_barge_in
speech_force_interrupted
speech_cancelled
stale_llm_chunk_dropped
stale_tts_stream_terminated
```

The old generation should no longer be allowed to emit LLM or TTS
output.

------------------------------------------------------------------------

# 32. Development Principles

Dispatch follows several design principles.

### Keep the audio path simple

The audio path remains:

``` text
LiveKit
  ↓
Silero
  ↓
Deepgram
  ↓
Gemini
  ↓
Rime
  ↓
LiveKit
```

The interruption system is layered around this path rather than
replacing it.

### Prefer streaming

LLM and TTS are streamed whenever supported.

### Avoid stale output

Every asynchronous response is associated with a conversational
generation.

### Measure actual playback

Server-side generation timing and browser playback timing are recorded
separately.

### Do not fabricate evaluation results

If a real browser measurement is unavailable, the result remains
unavailable.

------------------------------------------------------------------------

# 33. Technology Stack

``` text
Frontend
--------
React
Vite
LiveKit Client
LiveKit React Components


Backend
-------
Python
LiveKit Agents
LiveKit RTC


Voice
-----
Silero VAD
Deepgram Nova-2 STT
Gemini LLM
Rime TTS


Evaluation
----------
Python evaluation scripts
JSONL event logs
Browser playback telemetry
```

------------------------------------------------------------------------

# 34. Repository Screenshot Assets

The README expects these files:

``` text
docs/images/dispatch-web-app.png
docs/images/dispatch-architecture.png
```

Keep the filenames exactly as shown.

If you clone or copy the repository elsewhere, preserve the
`docs/images/` directory so the README images continue to render.

------------------------------------------------------------------------

# 35. Quick Start

``` bash
# Clone the repository
git clone <repository-url>
cd dispatch

# Create Python environment
python3 -m venv .venv
source .venv/bin/activate

# Install backend dependencies
pip install -r agent/requirements.txt

# Install frontend dependencies
cd client
npm install
cd ..

# Configure environment
cp .env.example .env
cp client/.env.example client/.env

# Start token server
python agent/token_server.py

# In another terminal:
python agent/main.py dev

# In another terminal:
cd client
npm run dev
```

Open the Vite URL and enable microphone access.

------------------------------------------------------------------------

# 36. Demo Summary

Dispatch demonstrates a voice-agent architecture designed around a
difficult real-world interaction:

``` text
Slow operation is running
        ↓
Technician interrupts
        ↓
Speech stops
        ↓
Old generation becomes invalid
        ↓
Old asynchronous work is cancelled or fenced
        ↓
New request becomes authoritative
        ↓
Gemini generates the new response
        ↓
Rime speaks the new response
```

The project is therefore not simply a speech-to-speech chatbot.

Its main engineering contribution is **safe conversational recovery
under interruption**, while maintaining a streaming, low-latency voice
pipeline.

------------------------------------------------------------------------

# 37. Final Architecture at a Glance

``` text
                         FIELD TECHNICIAN
                                │
                                │ voice
                                ▼
                    ┌──────────────────────┐
                    │   React + Vite UI    │
                    │ transcript / mic UI  │
                    └──────────┬───────────┘
                               │
                               │ WebRTC audio
                               ▼
                    ┌──────────────────────┐
                    │     LiveKit Room     │
                    └──────────┬───────────┘
                               │
                               ▼
                 ┌────────────────────────────┐
                 │     Python AgentSession    │
                 │                            │
                 │  Silero VAD               │
                 │       ↓                    │
                 │  Deepgram Nova-2           │
                 │       ↓                    │
                 │  Gemini                    │
                 │       ↓                    │
                 │  Rime WebSocket TTS        │
                 │       ↓                    │
                 │  LiveKit audio output      │
                 └────────────┬───────────────┘
                              │
                              ▼
                         Browser audio


          INTERRUPTION / RECOVERY LAYER

                 User starts speaking
                         ↓
                    Barge-in
                         ↓
                Generation invalidated
                         ↓
                 Audio buffer cleared
                         ↓
               Speech force interrupted
                         ↓
             Old LLM/TTS streams fenced
                         ↓
                New generation created
                         ↓
                 New answer produced
```

------------------------------------------------------------------------

## License

Add the project's applicable license here before public distribution.

## Status

Dispatch is currently structured as a functional voice-agent
demonstration and evaluation project focused on full-duplex interaction,
interruption handling, stale-result fencing, streaming TTS, and
measurable end-to-end voice latency.
