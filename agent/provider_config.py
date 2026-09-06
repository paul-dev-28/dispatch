import os
from dataclasses import asdict, dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    speaker: str
    language: str
    transport: str
    audio_format: str
    sample_rate: int


def get_provider_config() -> ProviderConfig:
    return ProviderConfig(
        provider="Rime",
        model=os.getenv("RIME_MODEL_ID", "mistv2"),
        speaker=os.getenv("RIME_SPEAKER", "cove"),
        language=os.getenv("RIME_LANGUAGE", "eng"),
        transport="WebSocket" if os.getenv("RIME_USE_WEBSOCKET", "true").lower() == "true" else "HTTP",
        audio_format=os.getenv("RIME_AUDIO_FORMAT", "pcm"),
        sample_rate=int(os.getenv("RIME_SAMPLE_RATE", "22050")),
    )


def public_provider_config() -> dict:
    return asdict(get_provider_config())


def validate_environment(require_live_credentials: bool = True) -> None:
    required = [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "RIME_API_KEY",
        "DEEPGRAM_API_KEY",
        "GROQ_API_KEY",
    ]
    if not require_live_credentials:
        return
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
