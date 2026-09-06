import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from livekit import api

from provider_config import public_provider_config, validate_environment

load_dotenv()
app = FastAPI(title="Dispatch token and metrics server")
# A comma-separated explicit client origin is required outside the local demo.
CLIENT_ORIGINS = os.getenv("CLIENT_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=CLIENT_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["content-type"])

EVENTS_PATH = Path(os.environ.get("EVAL_RESULTS_DIR", "./eval/results")) / "events.jsonl"


@app.get("/provider")
def provider():
    return public_provider_config()


@app.get("/metrics/latest")
def latest_metrics(n: int = 20):
    n = max(1, min(n, 200))
    if not EVENTS_PATH.exists():
        return {"events": []}
    events = []
    for line in EVENTS_PATH.read_text().splitlines()[-n:]:
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"events": events}


@app.post("/client-metrics")
def client_metrics(payload: dict):
    allowed = {"session_id", "turn_id", "generation", "speech_id", "event", "browser_epoch_ms", "browser_performance_ms", "details"}
    if not isinstance(payload, dict) or not payload.get("event"):
        raise HTTPException(status_code=400, detail="event is required")
    record = {k: payload[k] for k in allowed if k in payload}
    record.setdefault("turn_id", "client")
    record["source"] = "browser"
    record["timestamp"] = __import__("time").time()
    record["ts"] = record["timestamp"]
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")
    return {"ok": True}


@app.get("/clock")
def clock():
    import time
    return {"server_epoch_ms": time.time() * 1000}


@app.get("/health")
def health():
    return {"ok": True, "provider": public_provider_config()}


@app.get("/token")
def get_token(identity: str | None = None, room: str = "dispatch"):
    try:
        validate_environment()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    identity = identity or f"user-{uuid.uuid4().hex[:8]}"
    token = (
        api.AccessToken(
            os.environ["LIVEKIT_API_KEY"],
            os.environ["LIVEKIT_API_SECRET"],
        )
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .with_room_config(
            api.RoomConfiguration(
                agents=[
                    api.RoomAgentDispatch(
                        agent_name="dispatch-agent",
                    )
                ]
            )
        )
    )
    return {"token": token.to_jwt(), "url": os.environ["LIVEKIT_URL"], "room": room}


if __name__ == "__main__":
    import uvicorn
    validate_environment()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("TOKEN_SERVER_PORT", "8000")))
