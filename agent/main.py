import asyncio

from dotenv import load_dotenv

from livekit.agents import (
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
    inference,
    room_io,
)

from livekit.agents.metrics import (
    EOUMetrics,
    LLMMetrics,
    TTSMetrics,
)

from livekit.plugins import (
    deepgram,
    google,
    rime,
    silero,
)

from interruption import global_guard as guard
from logging_utils import log_event
from pipeline import FieldAssistant
from provider_config import (
    get_provider_config,
    validate_environment,
)

load_dotenv()


async def entrypoint(ctx: JobContext):
    """
    Dispatch voice-agent entrypoint.

    Interruption flow:

    1. VAD detects barge-in.
    2. The current generation is invalidated immediately.
    3. Any queued output audio is cleared.
    4. Current speech is forcefully interrupted.
    5. Old LLM/TTS streams see the stale generation and terminate.
    6. The finalized user turn receives a fresh generation.
    7. The new question is processed normally.
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

        llm=google.LLM(
            model="gemini-3.6-flash",
        ),

        tts=rime.TTS(
            model=cfg.model,
            speaker=cfg.speaker,
            lang=cfg.language,
            use_websocket=True,
            segment="bySentence",
            reduce_latency=True,
            sample_rate=cfg.sample_rate,
        ),

        use_tts_aligned_transcript=True,

        turn_handling={
            "turn_detection": inference.TurnDetector(),

            "endpointing": {
                "mode": "dynamic",
                "min_delay": 0.3,
                "max_delay": 3.0,
            },

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
            },

            "preemptive_generation": {
                "enabled": False,
            },
        },
    )

    @session.on("user_input_transcribed")
    def on_user_input(event):
        print(
            f"[USER TRANSCRIPT] "
            f"final={event.is_final} "
            f"text={event.transcript!r}"
        )

        if not event.is_final:
            return

        log_event(
            guard.turn_id,
            "turn_started",
            session_id=guard.session_id,
            generation=guard.snapshot(),
            transcript=event.transcript,
        )

    @session.on("overlapping_speech")
    def on_overlap(event):
        if not event.is_interruption:
            return

        # Invalidate the old generation FIRST.
        old_generation = guard.snapshot()

        new_generation, new_turn = guard.invalidate()

        log_event(
            new_turn,
            "barge_in_detected",
            session_id=guard.session_id,
            previous_generation=old_generation,
            generation=new_generation,
            interruption_probability=event.probability,
            detected_at=event.detected_at,
            detection_delay=event.detection_delay,
            total_duration=event.total_duration,
            prediction_duration=event.prediction_duration,
        )

        # Clear anything already queued for playback.
        try:
            if session.output.audio is not None:
                session.output.audio.clear_buffer()

                log_event(
                    new_turn,
                    "audio_buffer_cleared_on_barge_in",
                    session_id=guard.session_id,
                    generation=new_generation,
                )

        except Exception as exc:
            log_event(
                new_turn,
                "audio_buffer_clear_failed",
                session_id=guard.session_id,
                generation=new_generation,
                error=str(exc),
            )

        async def stop_current_speech():
            try:
                await session.interrupt(force=True)

                log_event(
                    new_turn,
                    "speech_force_interrupted",
                    session_id=guard.session_id,
                    generation=new_generation,
                )

            except Exception as exc:
                log_event(
                    new_turn,
                    "speech_force_interrupt_failed",
                    session_id=guard.session_id,
                    generation=new_generation,
                    error=str(exc),
                )

        asyncio.create_task(stop_current_speech())

    @session.on("agent_false_interruption")
    def on_false_interruption(event):
        log_event(
            guard.turn_id or turn_id,
            "false_interruption",
            session_id=guard.session_id,
            generation=guard.snapshot(),
        )

    @session.on("speech_created")
    def on_speech_created(event):
        speech_id = event.speech_handle.id

        speech_turn = guard.turn_id or turn_id
        speech_generation = guard.snapshot()

        speech_context[speech_id] = (
            speech_turn,
            speech_generation,
        )

        log_event(
            speech_turn,
            "speech_created",
            session_id=guard.session_id,
            generation=speech_generation,
            speech_id=speech_id,
            source=event.source,
        )

        def completed(handle):
            context = speech_context.get(speech_id)

            if context is None:
                return

            correlated_turn, correlated_generation = context

            log_event(
                correlated_turn,
                (
                    "speech_cancelled"
                    if handle.interrupted
                    else "speech_stopped"
                ),
                session_id=guard.session_id,
                generation=correlated_generation,
                speech_id=speech_id,
                exception=(
                    type(handle.exception()).__name__
                    if handle.exception()
                    else None
                ),
            )

            speech_context.pop(
                speech_id,
                None,
            )

        event.speech_handle.add_done_callback(completed)

    @session.on("metrics_collected")
    def on_metrics(event):
        metric = event.metrics

        common = {
            "session_id": guard.session_id,
            "generation": guard.snapshot(),
            "speech_id": getattr(
                metric,
                "speech_id",
                None,
            ),
        }

        if isinstance(metric, EOUMetrics):
            log_event(
                guard.turn_id or turn_id,
                "stt_finalize",
                end_of_utterance_delay=(
                    metric.end_of_utterance_delay
                ),
                **common,
            )

        elif isinstance(metric, LLMMetrics):
            log_event(
                guard.turn_id or turn_id,
                "llm_first_token",
                ttft=metric.ttft,
                **common,
            )

        elif isinstance(metric, TTSMetrics):
            log_event(
                guard.turn_id or turn_id,
                "rime_first_byte",
                ttfb=metric.ttfb,
                cancelled=metric.cancelled,
                streamed=metric.streamed,
                audio_duration=metric.audio_duration,
                **common,
            )

    await session.start(
        agent=FieldAssistant(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            text_output=room_io.TextOutputOptions(
                sync_transcription=True,
            ),
        ),
    )

    log_event(
        turn_id,
        "session_started",
        session_id=guard.session_id,
        generation=generation,
        provider=cfg.provider,
        model=cfg.model,
        speaker=cfg.speaker,
        language=cfg.language,
        transport=cfg.transport,
        audio_format=cfg.audio_format,
        sample_rate=cfg.sample_rate,
    )

    @session.on("conversation_item_added")
    def on_conversation_item(event):
        item = event.item

        if not hasattr(item, "text_content"):
            return

        print(
            "[CHAT HISTORY] "
            f"role={item.role} "
            f"interrupted="
            f"{getattr(item, 'interrupted', False)} "
            f"text={item.text_content!r}"
        )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="dispatch-agent",
        )
    )