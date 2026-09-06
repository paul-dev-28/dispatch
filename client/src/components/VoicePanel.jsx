import {
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";

import { useEffect, useState } from "react";

export default function VoicePanel() {
  const room = useRoomContext();
  const { state } = useVoiceAssistant();

  const [messages, setMessages] = useState([]);
  const [muted, setMuted] = useState(false);

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

  return (
    <div className="voice-panel">
      <div className="state-label">
        {state || "connecting"}
      </div>

      <div className="transcript">
        {messages.length === 0 ? (
          <div>Ask me anything.</div>
        ) : (
          messages.map((message) => (
            <div key={message.id}>
              <strong>{message.speaker}:</strong>{" "}
              {message.text}
            </div>
          ))
        )}
      </div>

      <button onClick={toggleMute}>
        {muted ? "Unmute" : "Mute"}
      </button>
    </div>
  );
}