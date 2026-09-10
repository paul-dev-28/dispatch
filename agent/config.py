"""Centralized environment configuration for the Dispatch voice agent."""
from __future__ import annotations

import os
from dataclasses import dataclass

def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value

def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return default if value is None else int(value)

def _env_float(name: str, default: float) -> float:
    value = _env(name)
    return default if value is None else float(value)

def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class Settings:
    # Credentials are intentionally never stored in source.
    livekit_url: str | None = _env("LIVEKIT_URL")
    livekit_api_key: str | None = _env("LIVEKIT_API_KEY")
    livekit_api_secret: str | None = _env("LIVEKIT_API_SECRET")
    rime_api_key: str | None = _env("RIME_API_KEY")
    deepgram_api_key: str | None = _env("DEEPGRAM_API_KEY")
    openai_api_key: str | None = _env("OPENAI_API_KEY")
    google_api_key: str | None = _env("GOOGLE_API_KEY")

    rime_model_id: str = _env("RIME_MODEL_ID", "mistv2") or "mistv2"
    rime_speaker: str = _env("RIME_SPEAKER", "cove") or "cove"
    rime_language: str = _env("RIME_LANGUAGE", "eng") or "eng"
    rime_audio_format: str = _env("RIME_AUDIO_FORMAT", "pcm") or "pcm"
    rime_sample_rate: int = _env_int("RIME_SAMPLE_RATE", 22050)
    rime_use_websocket: bool = _env_bool("RIME_USE_WEBSOCKET", True)

    llm_model: str = _env("LLM_MODEL", "llama-3.3-70b-versatile") or "gpt-4o-mini"

    slow_tool_delay_s: float = _env_float("SLOW_TOOL_DELAY_S", 4.0)
    fast_tool_delay_s: float = _env_float("FAST_TOOL_DELAY_S", 0.2)

    demo_part_number: str = _env("DEMO_PART_NUMBER", "9911") or "9911"
    demo_interrupt_delay_s: float = _env_float("DEMO_INTERRUPT_DELAY_S", 1.0)
    eval_trials: int = _env_int("EVAL_TRIALS", 10)
    eval_latency_turns: int = _env_int("EVAL_LATENCY_TURNS", 20)

    token_server_port: int = _env_int("TOKEN_SERVER_PORT", 8000)
    client_origins: str = _env(
        "CLIENT_ORIGINS", "http://localhost:5173"
    ) or "http://localhost:5173"

    def require_credentials(self) -> None:
        required = {
            "LIVEKIT_URL": self.livekit_url,
            "LIVEKIT_API_KEY": self.livekit_api_key,
            "LIVEKIT_API_SECRET": self.livekit_api_secret,
            "RIME_API_KEY": self.rime_api_key,
            "DEEPGRAM_API_KEY": self.deepgram_api_key,
            "OPENAI_API_KEY": self.openai_api_key,
            "GOOGLE_API_KEY": self.google_api_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )

    @property
    def allowed_origins(self) -> list[str]:
        return [x.strip() for x in self.client_origins.split(",") if x.strip()]

settings = Settings()
