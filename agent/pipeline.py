import inspect
import re
from collections.abc import AsyncGenerator, AsyncIterable

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

Keep spoken answers concise and natural unless the user asks for detail, so
that the spoken audio and the live transcript stay tightly in sync and no
voice backlog builds up.

When the user interrupts your answer with a new question, immediately
discard the previous response and answer the new question. Never continue
speaking the previous answer after an interruption.
""".strip()


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


class FieldAssistant(Agent):

    def __init__(self):
        super().__init__(
            instructions=SYSTEM_PROMPT,
            allow_interruptions=True,
        )

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
                if guard.is_stale(speech_generation):
                    log_event(
                        guard.turn_id,
                        "stale_llm_chunk_dropped",
                        session_id=guard.session_id,
                        generation=guard.snapshot(),
                        stale_generation=speech_generation,
                    )
                    return

                yield chunk

        finally:
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
                        "llm_stream_close_error",
                        session_id=guard.session_id,
                        generation=guard.snapshot(),
                        error=str(exc),
                    )

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

                # The generation that produced this TTS stream
                # is no longer current.
                if guard.is_stale(speech_generation):

                    log_event(
                        guard.turn_id,
                        "stale_tts_stream_terminated",
                        session_id=guard.session_id,
                        generation=guard.snapshot(),
                        stale_generation=speech_generation,
                    )

                    # Clear any audio that may already have been
                    # queued by this stale response.
                    if self.session.output.audio is not None:
                        self.session.output.audio.clear_buffer()

                    return

                yield frame

        finally:
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

        # Do not treat simple backchannels as a new question.
        if is_backchannel_only(text):
            log_event(
                guard.turn_id,
                "backchannel_ignored",
                session_id=guard.session_id,
                generation=guard.snapshot(),
                transcript=text,
            )

            raise StopResponse()

        # Every completed user turn gets a new generation.
        # This guarantees that anything belonging to the
        # previous response becomes stale.
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

        # Clear queued output belonging to the previous response.
        if self.session.output.audio is not None:
            self.session.output.audio.clear_buffer()

            log_event(
                new_turn,
                "audio_buffer_cleared",
                session_id=guard.session_id,
                generation=new_generation,
            )

        # Stop any currently active speech before the new response
        # is generated.
        try:
            await self.session.interrupt(
                force=True
            )

            log_event(
                new_turn,
                "speech_interrupted_for_new_turn",
                session_id=guard.session_id,
                generation=new_generation,
            )

        except Exception as exc:
            log_event(
                new_turn,
                "speech_interrupt_for_new_turn_failed",
                session_id=guard.session_id,
                generation=new_generation,
                error=str(exc),
            )

        # Explicit stop command.
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

        # Handle commands such as:
        # "stop explaining quantum physics"
        prefix_match = _STOP_PREFIX_RE.match(text)

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