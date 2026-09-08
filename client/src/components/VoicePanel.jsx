import {
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";

import { useEffect, useRef, useState } from "react";
import StatusBoard from "./StatusBoard.jsx";

const STATE_COPY = {
  connecting: { title: "Connecting", subtitle: "Setting things up" },
  initializing: { title: "Connecting", subtitle: "Setting things up" },
  listening: { title: "Listening", subtitle: "Ready to hear you" },
  thinking: { title: "Thinking", subtitle: "Working on a reply" },
  speaking: { title: "Speaking", subtitle: "Playing the response" },
};

function MicIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 15a3.5 3.5 0 0 0 3.5-3.5V6a3.5 3.5 0 1 0-7 0v5.5A3.5 3.5 0 0 0 12 15Z" />
      <path d="M19 11.5a7 7 0 0 1-14 0" />
      <line x1="12" y1="18.5" x2="12" y2="22" />
      <line x1="8.5" y1="22" x2="15.5" y2="22" />
    </svg>
  );
}

function MicOffIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 15a3.5 3.5 0 0 0 3.5-3.5V6a3.5 3.5 0 0 0-6.8-1.1" />
      <path d="M8.5 8.5V11.5a3.5 3.5 0 0 0 5 3.17" />
      <path d="M19 11.5a7 7 0 0 1-9.8 6.4" />
      <path d="M5 11.5a7 7 0 0 0 1.3 4.06" />
      <line x1="12" y1="18.5" x2="12" y2="22" />
      <line x1="8.5" y1="22" x2="15.5" y2="22" />
      <line x1="3" y1="3" x2="21" y2="21" />
    </svg>
  );
}

export default function VoicePanel() {
  const room = useRoomContext();
  const { state } = useVoiceAssistant();

  const [messages, setMessages] = useState([]);
  const [muted, setMuted] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!room) return;

    const handleTranscription = async (reader, participantInfo) => {
      try {
        const participantIdentity =
          participantInfo?.identity ?? participantInfo;

        const attributes = reader.info?.attributes ?? {};

        const segmentId = attributes["lk.segment_id"];
        const isFinal =
          attributes["lk.transcription_final"] === "true";

        const trackId =
          attributes["lk.transcribed_track_id"];

        if (!segmentId) return;

        const isUser =
          participantIdentity === room.localParticipant.identity;

        let accumulatedText = "";

        for await (const chunk of reader) {
            const delta =
              typeof chunk === "string"
                ? chunk
                : chunk.current ?? "";

            accumulatedText += delta;

            const text = accumulatedText.trim();
          if (!text) continue;

          setMessages((previous) => {
            const message = {
              id: segmentId,
              speaker: isUser ? "You" : "AI",
              text,
              final: isFinal,
              trackId,
            };

            const index = previous.findIndex(
              (item) => item.id === segmentId
            );

            if (index === -1) {
              return [...previous, message];
            }

            const updated = [...previous];
            updated[index] = message;
            return updated;
          });
        }
      } catch (error) {
        console.error("Transcription stream error:", error);
      }
    };

    room.registerTextStreamHandler(
      "lk.transcription",
      handleTranscription
    );

    return () => {
      room.unregisterTextStreamHandler(
        "lk.transcription"
      );
    };
  }, [room]);

  const toggleMute = async () => {
    const next = !muted;

    await room.localParticipant.setMicrophoneEnabled(!next);

    setMuted(next);
  };

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages]);

  const normalizedState = (state || "connecting").toLowerCase();
  const copy = STATE_COPY[normalizedState] || {
    title: state || "Connecting",
    subtitle: "",
  };

  return (
    <div className="chat-layout">
      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="chat-empty">Ask me anything.</div>
        ) : (
          messages.map((message) => (
            <div
              key={message.id}
              className={`message-row ${
                message.speaker === "You" ? "from-user" : "from-ai"
              }`}
            >
              <div className="message-bubble">{message.text}</div>
            </div>
          ))
        )}
      </div>

      <div className="voice-box">
        <div className="voice-box-main">
          <span className={`state-dot state-${normalizedState}`} />
          <div className="voice-box-text">
            <strong>{copy.title}</strong>
            {copy.subtitle ? <span>{copy.subtitle}</span> : null}
          </div>

          <button
            type="button"
            className={`mic-button ${muted ? "is-muted" : "is-active"}`}
            onClick={toggleMute}
            title={muted ? "Unmute microphone" : "Mute microphone"}
            aria-label={muted ? "Unmute microphone" : "Mute microphone"}
            aria-pressed={muted}
          >
            {muted ? <MicOffIcon /> : <MicIcon />}
          </button>
        </div>

        <StatusBoard />
      </div>
    </div>
  );
}