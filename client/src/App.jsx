import { LiveKitRoom, StartAudio } from "@livekit/components-react";
import { useEffect, useState } from "react";
import ProviderBadge from "./components/ProviderBadge.jsx";
import PlaybackMetrics from "./components/PlaybackMetrics.jsx";
import StatusBoard from "./components/StatusBoard.jsx";
import VoicePanel from "./components/VoicePanel.jsx";

const TOKEN_SERVER = import.meta.env.VITE_TOKEN_SERVER_URL || "http://localhost:8000";

export default function App() {
  const [conn, setConn] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
  const room = `dispatch-${crypto.randomUUID()}`;

  fetch(`${TOKEN_SERVER}/token?room=${room}`)
    .then((r) =>
      r.ok
        ? r.json()
        : r.json().then((d) =>
            Promise.reject(new Error(d.detail || "Token server error"))
          )
    )
    .then(setConn)
    .catch((e) => setError(e.message));
}, []);

  if (error) return <div className="app-shell error">Could not reach token server: {error}</div>;
  if (!conn) return <div className="app-shell">Connecting…</div>;

  return (
    <LiveKitRoom serverUrl={conn.url} token={conn.token} audio connect className="app-shell">
      <StartAudio label="Enable audio" />
      <ProviderBadge />
      <VoicePanel />
      <StatusBoard />
      <PlaybackMetrics />
    </LiveKitRoom>
  );
}
