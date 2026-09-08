import inspect
import re
from collections.abc import AsyncIterable, AsyncGenerator

from livekit import rtc
from livekit.agents import (
    Agent,
    ChatContext,
    ChatMessage,
    ModelSettings,
    StopResponse,
)

from interruption import global_guard as guard
from logging_utils import log_event


SYSTEM_PROMPT = """
You are Dispatch, a general-purpose AI voice assistant speaking out loud over
a live audio connection. Your text is converted directly to speech and also
shown as a live transcript — it is never rendered as formatted text.

Never use Markdown syntax (no #, *, -, |, tables, code fences, headers) or
HTML tags (no <br>, <b>, <ul>, etc). Never use bullet points or numbered
lists. Speak in plain, natural spoken sentences the way a person would say
them out loud. If you need to enumerate several things, say them as a
sentence ("There are three factors: first ... second ... third ...")
instead of a list or table.

Answer the user's actual questions using your language-model knowledge and
the real conversation history. You are not a scripted demo.

Never invent previous messages, fake tool results, measurements, parts,
procedures, or conversations.

You can answer general knowledge, engineering, programming, mathematics,
science, troubleshooting, and everyday questions. For follow-up questions,
use the actual conversation history.

If you do not know something, say so rather than making it up. If a question
is genuinely ambiguous and the missing information matters, ask one concise
clarification.

Keep spoken answers concise and natural unless the user asks for detail.
Always speak in sync with text and generate the text slowly as compared to the voice generted.
Always keep in mind to give short answers such that no voice backlog occurs.
If a new qustion is asked on interrupted, discard the previous voice baklog or info and start speaking the text that is generated.
""".strip()


# ==========================================================
# STOP / CANCEL CONTROL
# ==========================================================

_FILLERS = (
    r"(?:please|now|speaking|talking|explaining|"
    r"reading|going|that|it|chat)"
)

_STOP_COMMAND_RE = re.compile(
    rf"^\s*"
    rf"(?:please\s+)?"
    rf"(?:stop|cancel)"
    rf"(?:\s+{_FILLERS})*"
    rf"\s*[.!]?\s*$",
    re.IGNORECASE,
)

_STOP_PREFIX_RE = re.compile(
    rf"^\s*"
    rf"(?:please\s+)?"
    rf"(?:stop|cancel)"
    rf"(?:\s+{_FILLERS})*"
    rf"\s*[.!,]?\s*",
    re.IGNORECASE,
)


def is_stop_command(text: str) -> bool:
    if not text:
        return False

    return bool(
        _STOP_COMMAND_RE.match(text.strip())
    )


# ==========================================================
# BARE BACKCHANNEL / FILLER DETECTION
# ==========================================================
#
# Short acknowledgement / thinking-out-loud words that STT can
# finalize as a complete user turn (e.g. "So", "Um", "Okay") even
# though the speaker hasn't actually asked anything yet. Forwarding
# these to the LLM as a real turn causes the agent to awkwardly
# keep rambling on the previous topic instead of waiting for the
# actual question. These must NEVER invalidate the current
# generation or interrupt in-progress speech — they are simply
# ignored so the in-flight answer keeps playing, and the next real
# question is answered (and spoken) fresh and on-topic.

_BACKCHANNEL_RE = re.compile(
    r"^\s*"
    r"(?:so|um+|uh+|erm+|hmm+|okay|ok|well|"
    r"right|yeah|yep|uh-huh|mm-hmm|alright)"
    r"\s*[.,!?]?\s*$",
    re.IGNORECASE,
)


def is_backchannel_only(text: str) -> bool:
    if not text:
        return False

    return bool(
        _BACKCHANNEL_RE.match(text.strip())
    )


# ==========================================================
# AGENT
# ==========================================================

class FieldAssistant(Agent):

    def __init__(self):
        super().__init__(
            instructions=SYSTEM_PROMPT,
            allow_interruptions=True,
        )

    # ======================================================
    # HARD LLM GENERATION GATE
    # ======================================================
    #
    # Capture the generation when this response starts.
    #
    # If a newer user turn invalidates that generation, the
    # old LLM stream is immediately abandoned.
    #
    # IMPORTANT:
    # We deliberately do NOT split provider chunks or insert
    # artificial sleeps. LiveKit/provider-native streaming is
    # preserved exactly.

    async def llm_node(
        self,
        chat_ctx: ChatContext,
        tools: list,
        model_settings: ModelSettings,
    ):
        speech_generation = guard.snapshot()

        log_event(
            guard.turn_id,
            "llm_generation_started",
            session_id=guard.session_id,
            generation=speech_generation,
        )

        stream = Agent.default.llm_node(
            self,
            chat_ctx,
            tools,
            model_settings,
        )

        if inspect.iscoroutine(stream):
            stream = await stream

        try:
            async for chunk in stream:

                # --------------------------------------------------
                # HARD STALE-GENERATION CHECK
                # --------------------------------------------------

                if guard.is_stale(speech_generation):
                    log_event(
                        guard.turn_id,
                        "stale_llm_chunk_dropped",
                        session_id=guard.session_id,
                        generation=guard.snapshot(),
                        stale_generation=speech_generation,
                    )

                    return

                # --------------------------------------------------
                # PRESERVE NATIVE PROVIDER CHUNK
                # --------------------------------------------------

                yield chunk

        finally:
            # Explicitly close the provider stream when an
            # interruption causes us to leave early.
            close_stream = getattr(
                stream,
                "aclose",
                None,
            )

            if close_stream is not None:
                await close_stream()

    # ======================================================
    # HARD TTS GENERATION GATE
    # ======================================================

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncGenerator[rtc.AudioFrame, None]:

        speech_generation = guard.snapshot()

        log_event(
            guard.turn_id,
            "tts_generation_started",
            session_id=guard.session_id,
            generation=speech_generation,
        )

        stream = Agent.default.tts_node(
            self,
            text,
            model_settings,
        )

        if inspect.iscoroutine(stream):
            stream = await stream

        try:
            async for frame in stream:

                # --------------------------------------------------
                # HARD STALE-GENERATION CHECK
                # --------------------------------------------------

                if guard.is_stale(speech_generation):
                    log_event(
                        guard.turn_id,
                        "stale_tts_stream_terminated",
                        session_id=guard.session_id,
                        generation=guard.snapshot(),
                        stale_generation=speech_generation,
                    )

                    # Returning here only stops US from handing over
                    # any MORE frames. It does nothing about frames
                    # from this same stale generation that were
                    # already forwarded to session.output.audio
                    # before the generation flipped (Rime can
                    # synthesize faster than realtime and get ahead
                    # of playback). Those are still sitting in the
                    # output's playback queue and would otherwise
                    # keep playing on top of / instead of the new
                    # answer -- this is the "previous answer keeps
                    # talking" backlog. Drop them here too.
                    if self.session.output.audio is not None:
                        self.session.output.audio()

                    # IMPORTANT:
                    # Stop the TTS stream completely.
                    return

                yield frame

        finally:
            # --------------------------------------------------
            # CLOSE THE OLD TTS STREAM
            # --------------------------------------------------

            close_stream = getattr(
                stream,
                "aclose",
                None,
            )

            if close_stream is not None:
                try:
                    await close_stream()
                except Exception as exc:
                    log_event(
                        guard.turn_id,
                        "tts_stream_close_error",
                        session_id=guard.session_id,
                        generation=guard.snapshot(),
                        error=str(exc),
                    )

    async def on_user_turn_completed(
        self,
        turn_ctx: ChatContext,
        new_message: ChatMessage,
    ) -> None:

        text = (
            new_message.text_content or ""
        ).strip()

        # --------------------------------------------------
        # BARE BACKCHANNEL / FILLER — IGNORE ENTIRELY
        # --------------------------------------------------
        #
        # A lone "so", "um", "okay", etc. is not a real turn.
        # Do this check FIRST, before any generation invalidation,
        # buffer clearing, or forced interruption — the in-flight
        # answer must keep playing untouched, and no reply should
        # be generated for the filler itself. The next real
        # question then invalidates/generates/speaks normally.

        if is_backchannel_only(text):
            log_event(
                guard.turn_id,
                "backchannel_ignored",
                session_id=guard.session_id,
                generation=guard.snapshot(),
                transcript=text,
            )

            raise StopResponse()

        # --------------------------------------------------
        # HARD GENERATION INVALIDATION
        # --------------------------------------------------
        #
        # This is the authoritative user-turn boundary.
        #
        # The previous generation is invalidated BEFORE waiting
        # for LiveKit's existing speech interruption to finish.

        old_generation = guard.snapshot()

        new_generation, new_turn = guard.invalidate()

        log_event(
            new_turn,
            "turn_cutoff",
            session_id=guard.session_id,
            previous_generation=old_generation,
            generation=new_generation,
            transcript=text,
        )

        # --------------------------------------------------
        # CLEAR ALREADY-BUFFERED AUDIO
        # --------------------------------------------------

        if self.session.output.audio is not None:
            self.session.output.audio.clear_buffer()

        log_event(
            new_turn,
            "audio_buffer_cleared",
            session_id=guard.session_id,
            generation=new_generation,
        )

        # --------------------------------------------------
        # FORCE LIVEKIT INTERRUPTION
        # --------------------------------------------------

        await self.session.interrupt(
            force=True
        )

        # --------------------------------------------------
        # PURE STOP
        # --------------------------------------------------

        if is_stop_command(text):
            log_event(
                guard.turn_id,
                "control_command",
                session_id=guard.session_id,
                generation=guard.snapshot(),
                command="stop",
                transcript=text,
            )

            raise StopResponse()

        # --------------------------------------------------
        # STOP + NEW QUESTION
        # --------------------------------------------------

        prefix_match = _STOP_PREFIX_RE.match(
            text
        )

        if prefix_match:
            remainder = (
                text[prefix_match.end():]
                .strip()
            )

            if (
                not remainder
                or is_stop_command(remainder)
            ):
                log_event(
                    guard.turn_id,
                    "control_command",
                    session_id=guard.session_id,
                    generation=guard.snapshot(),
                    command="stop",
                    transcript=text,
                )

                raise StopResponse()

            new_message.content = [
                remainder
            ]

            log_event(
                guard.turn_id,
                "barge_in_prefix_stripped",
                session_id=guard.session_id,
                generation=guard.snapshot(),
                original_transcript=text,
                cleaned_transcript=remainder,
            )

        # --------------------------------------------------
        # NORMAL TURN
        # --------------------------------------------------
        #
        # The framework now generates the new response normally.