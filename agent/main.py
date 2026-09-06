import os

from dotenv import load_dotenv
from livekit.agents import (
    AgentSession,
    inference,
    JobContext,
    WorkerOptions,
    cli,
    room_io,
    RoomInputOptions,
)
from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics
from livekit.plugins import deepgram, groq, rime, silero

from interruption import global_guard as guard
from logging_utils import log_event
from pipeline import FieldAssistant
from provider_config import get_provider_config, validate_environment

load_dotenv()


async def entrypoint(ctx: JobContext):
    """Run the pinned LiveKit Agents 1.8 voice path.

    Agents 1.8 emits ``overlapping_speech``; it does not expose the unsupported
    ``user_interruption_detected`` event. Only a confirmed overlap advances a generation.

    Interruption ownership (single authoritative path):
      - turn_handling.interruption (mode="vad") owns ALL barge-in / playback
        cancellation automatically. No handler in this file calls
        session.interrupt() for ordinary barge-in.
      - FieldAssistant.on_user_turn_completed (pipeline.py) owns blocking a
        reply for recognized voice-control commands ("stop", "cancel", ...),
        with a defensive/idempotent session.interrupt() as a safety net for
        that specific case only.
    """
    validate_environment()
    await ctx.connect()
    cfg = get_provider_config()
    guard.set_session(ctx.room.name)
    generation, turn_id = guard.new_turn()
    
    speech_context: dict[str, tuple[str, int]] = {}
    session = AgentSession(
        vad=silero.VAD.load(
            min_silence_duration=0.25,
            prefix_padding_duration=0.20,
        ),

        stt=deepgram.STT(
            model="nova-2",
            language="en",
        ),

        llm=groq.LLM(
            model="openai/gpt-oss-20b",
        ),

        tts=rime.TTS(
            model=cfg.model,
            speaker=cfg.speaker,
            lang=cfg.language,
            use_websocket=True,
            segment="immediate",
            reduce_latency=True,
            sample_rate=cfg.sample_rate,
        ),

        use_tts_aligned_transcript=True,

        turn_handling={
            "turn_detection": inference.TurnDetector(),
            "endpointing": {
                "mode": "fixed",
                "min_delay": 0.20,
                "max_delay": 0.5,
            },
            "interruption": {
                "enabled": True,
                "mode": "vad",

                # 0.08s/1 word (below LiveKit's documented default of 0.5s)
                # was triggering on breaths, mic noise, and single-syllable
                # blips before STT had confirmed a real word. 0.2s + 2 words
                # keeps barge-in snappy but requires actual confirmed speech.
                "min_duration": 0.2,
                "min_words": 2,

                # 0.9s (below LiveKit's documented default of 2.0s) was
                # shorter than STT finalization time for longer sentences,
                # so the framework was classifying genuine new turns as
                # false interruptions and resuming stale agent speech
                # mid-sentence instead of waiting for the real new turn.
                "false_interruption_timeout": 1.8,
                "resume_false_interruption": True,

                "discard_audio_if_uninterruptible": True,

                "backchannel_boundary": (0.25, 0.25),
            },
        },
    )

    @session.on("user_input_transcribed")
    def on_user_input(event):
        print(
            f"[USER TRANSCRIPT] final={event.is_final} "
            f"text={event.transcript!r}"
        )

        if not event.is_final:
            return

        new_generation, new_turn = guard.new_turn()

        log_event(
            new_turn,
            "turn_started",
            session_id=guard.session_id,
            generation=new_generation,
            transcript=event.transcript,
        )
        # NOTE: a per-turn "user_state_changed" listener that forced
        # session.interrupt(force=True) used to be registered here. It has
        # been removed for two reasons: (1) it re-registered a new listener
        # on every finalized turn, stacking duplicate handlers over the
        # session; (2) it bypassed the configured
        # interruption.min_duration/min_words gating above and duplicated
        # what turn_handling.interruption already does automatically.

    @session.on("overlapping_speech")
    def on_overlap(event):
        # NOTE: interruption.mode is "vad" here, so the adaptive interruption
        # detector that emits this event isn't constructed and this handler
        # will not currently fire. It's kept as a log-only observer in case
        # the mode is ever switched to "adaptive". Playback cancellation is
        # owned entirely by turn_handling.interruption (barge-in) and by the
        # explicit session.interrupt() in FieldAssistant.on_user_turn_completed
        # for recognized control commands — do not add a session.interrupt()
        # call here too.
        if not event.is_interruption:
            return

        old_generation, old_turn = guard.snapshot(), guard.turn_id
        new_generation, new_turn = guard.invalidate()

        log_event(
            new_turn,
            "barge_in_detected",
            session_id=guard.session_id,
            previous_turn_id=old_turn,
            previous_generation=old_generation,
            generation=new_generation,
            interruption_probability=event.probability,
            agent_state="speaking",
            current_speech_id=next(reversed(speech_context), None),
            detected_at=event.detected_at,
            detection_delay=event.detection_delay,
            total_duration=event.total_duration,
            prediction_duration=event.prediction_duration,
        )

    @session.on("agent_false_interruption")
    def on_false_interruption(event):
        log_event(guard.turn_id or turn_id, "false_interruption", session_id=guard.session_id,
                  generation=guard.snapshot())

    @session.on("speech_created")
    def on_speech_created(event):
        speech_id = event.speech_handle.id
        speech_turn, speech_generation = guard.turn_id or turn_id, guard.snapshot()
        speech_context[speech_id] = (speech_turn, speech_generation)
        log_event(speech_turn, "speech_created", session_id=guard.session_id,
                  generation=speech_generation, speech_id=speech_id, source=event.source)

        def completed(handle):
            correlated_turn, correlated_generation = speech_context[speech_id]
            log_event(correlated_turn, "speech_cancelled" if handle.interrupted else "speech_stopped",
                      session_id=guard.session_id, generation=correlated_generation,
                      speech_id=speech_id,
                      exception=type(handle.exception()).__name__ if handle.exception() else None)
        event.speech_handle.add_done_callback(completed)

    @session.on("metrics_collected")
    def on_metrics(event):
        metric = event.metrics
        common = {"session_id": guard.session_id, "generation": guard.snapshot(),
                  "speech_id": getattr(metric, "speech_id", None)}
        if isinstance(metric, EOUMetrics):
            log_event(guard.turn_id or turn_id, "stt_finalize", end_of_utterance_delay=metric.end_of_utterance_delay, **common)
        elif isinstance(metric, LLMMetrics):
            log_event(guard.turn_id or turn_id, "llm_first_token", ttft=metric.ttft, **common)
        elif isinstance(metric, TTSMetrics):
            log_event(guard.turn_id or turn_id, "rime_first_byte", ttfb=metric.ttfb,
                      cancelled=metric.cancelled, streamed=metric.streamed,
                      audio_duration=metric.audio_duration, **common)

    await session.start(
        agent=FieldAssistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            text_output=room_io.TextOutputOptions(
                sync_transcription=True,
            ),
        ),
    )

    log_event(turn_id, "session_started", session_id=guard.session_id, generation=generation,
              provider=cfg.provider, model=cfg.model, speaker=cfg.speaker,
              language=cfg.language, transport=cfg.transport,
              audio_format=cfg.audio_format, sample_rate=cfg.sample_rate)

    @session.on("conversation_item_added")
    def on_conversation_item(event):
        item = event.item

        if not hasattr(item, "text_content"):
            return

        print(
            f"[CHAT HISTORY] "
            f"role={item.role} "
            f"interrupted={getattr(item, 'interrupted', False)} "
            f"text={item.text_content!r}"
        )

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="dispatch-agent",
        )
    )