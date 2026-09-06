import { useEffect, useState } from "react";

const TOKEN_SERVER = import.meta.env.VITE_TOKEN_SERVER_URL || "http://localhost:8000";

export default function ProviderBadge() {
  const [config, setConfig] = useState(null);

  useEffect(() => {
    fetch(`${TOKEN_SERVER}/provider`)
      .then((r) => r.ok ? r.json() : Promise.reject(new Error("provider unavailable")))
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);

  return (
    <div className="provider-badge">
      <span className={`dot ${config ? "" : "offline"}`} />
      <div>
        <strong>{config?.provider || "Provider unavailable"}</strong>
        <span>{config ? ` · ${config.model} · ${config.speaker} · ${config.language} · ${config.transport}` : " · Configuration unavailable"}</span>
      </div>
    </div>
  );
}
