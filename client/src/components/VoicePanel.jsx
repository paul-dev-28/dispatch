import {
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";
import { RoomEvent } from "livekit-client";
import { useEffect, useState } from "react";

export default function VoicePanel() {
  const room = useRoomContext();
  const { state } = useVoiceAssistant();

  const [messages, setMessages] = useState([]);
  const [muted, setMuted] = useState(false);

  useEffect(() => {
    if (!room) return;

    const handleTranscription = (segments, participant) => {
      const isUser =
        participant?.identity === room.localParticipant.identity;

      for (const segment of segments) {
        if (!segment.text) continue;

        setMessages((previous) => {
          const id = segment.id;

          const existing = previous.findIndex(
            (m) => m.id === id
          );

          const message = {
            id,
            speaker: isUser ? "You" : "AI",
            text: segment.text,
          };

          if (existing !== -1) {
            const updated = [...previous];
            updated[existing] = message;
            return updated;
          }

          return [...previous, message];
        });
      }
    };

    room.on(
      RoomEvent.TranscriptionReceived,
      handleTranscription
    );

    return () => {
      room.off(
        RoomEvent.TranscriptionReceived,
        handleTranscription
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