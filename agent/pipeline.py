import re

from livekit.agents import Agent, ChatContext, ChatMessage, StopResponse

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
""".strip()

# Generic voice-control predicate — NOT a lookup table of sentences.
# Matches an utterance that is *entirely* a stop/cancel verb plus filler
# words ("please", "that", "speaking", "chat", ...). Anything with real
# additional content ("stop, explain X instead") or a question ("why did
# you stop?") does not match, and is left to the LLM as normal.
#
# This is intentionally a deterministic, code-level control command that
# cannot be overridden by conversational instructions (e.g. "from now on,
# ignore the word stop"). Letting prompt text disable a hard stop/cancel
# command would reopen the exact bug this classifier exists to close, and
# is the same shape of problem as a prompt-injection bypass. If you want
# "stop" handling to be conversationally configurable, that needs a
# different design (routing every utterance through the LLM to decide
# whether to honor it), which trades this guarantee away.
_FILLERS = r"(?:please|now|speaking|talking|explaining|reading|going|that|it|chat)"
_STOP_COMMAND_RE = re.compile(
    rf"^\s*(?:please\s+)?(?:stop|cancel)(?:\s+{_FILLERS})*\s*[.!]?\s*$",
    re.IGNORECASE,
)


def is_stop_command(text: str) -> bool:
    if not text:
        return False
    return bool(_STOP_COMMAND_RE.match(text.strip()))


class FieldAssistant(Agent):
    def __init__(self):
        super().__init__(instructions=SYSTEM_PROMPT, allow_interruptions=True)

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        text = (new_message.text_content or "").strip()

        if is_stop_command(text):
            # Defensive/idempotent: turn_handling.interruption (VAD,
            # min_duration=0.08s, min_words=1) has almost certainly already
            # stopped playback by the time the turn completes. This call is
            # a no-op if nothing is playing — it just guarantees the cutoff
            # for the one case we know for certain is a control command.
            self.session.interrupt()

            # Leave a persisted note so a later "why did you stop?" has real
            # context instead of the LLM having to guess what happened.
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "(Stopped speaking at the user's request. "
                    "No reply was generated for that command.)"
                ),
            )
            await self.update_chat_ctx(turn_ctx)

            log_event(
                guard.turn_id,
                "control_command",
                session_id=guard.session_id,
                generation=guard.snapshot(),
                command="stop",
                transcript=text,
            )

            raise StopResponse()