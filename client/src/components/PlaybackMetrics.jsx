import { AudioTrack, useTracks } from "@livekit/components-react";
import { Track } from "livekit-client";
import { useEffect, useRef } from "react";
import { reportClientMetric, syncServerClock } from "../lib/playbackMetrics.js";

const TOKEN_SERVER =
  import.meta.env.VITE_TOKEN_SERVER_URL || "http://localhost:8000";

async function getCurrentTurn() {
  try {
    const response = await fetch(
      `${TOKEN_SERVER}/metrics/latest?n=100`,
      { cache: "no-store" }
    );

    const data = await response.json();
    const events = data.events || [];

    const rimeEvent = [...events]
      .reverse()
      .find((event) => event.event === "rime_first_byte");

    if (!rimeEvent) return null;

    return {
      turn_id: rimeEvent.turn_id,
      speech_id: rimeEvent.speech_id,
      session_id: rimeEvent.session_id,
      generation: rimeEvent.generation,
    };
  } catch (_) {
    return null;
  }
}

function InstrumentedAudio({ trackRef }) {
  const audioRef = useRef(null);
  const turnRef = useRef(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return undefined;

    const onPlaying = async () => {
      const currentTurn = await getCurrentTurn();

      if (currentTurn) {
        turnRef.current = currentTurn;
      }

      await reportClientMetric("client_playback_started", {
        participant_identity: trackRef.participant.identity,
        track_sid: trackRef.publication.trackSid,
        ...(currentTurn || {}),
      });
    };

    const onPause = async () => {
      await reportClientMetric("client_playback_stopped", {
        participant_identity: trackRef.participant.identity,
        track_sid: trackRef.publication.trackSid,
        ...(turnRef.current || {}),
      });
    };

    const onEnded = async () => {
      await reportClientMetric("client_playback_ended", {
        participant_identity: trackRef.participant.identity,
        track_sid: trackRef.publication.trackSid,
        ...(turnRef.current || {}),
      });
    };

    audio.addEventListener("playing", onPlaying);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);

    return () => {
      audio.removeEventListener("playing", onPlaying);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
    };
  }, [trackRef]);

  return <AudioTrack ref={audioRef} trackRef={trackRef} volume={1} />;
}

export default function PlaybackMetrics() {
  const tracks = useTracks([
    Track.Source.Microphone,
    Track.Source.Unknown,
  ]).filter(
    (ref) =>
      ref?.participant &&
      !ref.participant.isLocal &&
      ref.publication?.kind === Track.Kind.Audio
  );

  useEffect(() => {
    syncServerClock();

    const id = setInterval(syncServerClock, 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="audio-renderer" aria-hidden="true">
      {tracks.map((trackRef) => (
        <InstrumentedAudio
          key={`${trackRef.participant.identity}-${trackRef.publication.trackSid}`}
          trackRef={trackRef}
        />
      ))}
    </div>
  );
}