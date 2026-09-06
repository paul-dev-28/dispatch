const TOKEN_SERVER = import.meta.env.VITE_TOKEN_SERVER_URL || "http://localhost:8000";

let serverOffsetMs = 0;

export async function syncServerClock() {
  const t0 = performance.now();
  const wall0 = Date.now();
  try {
    const response = await fetch(`${TOKEN_SERVER}/clock`, { cache: "no-store" });
    const data = await response.json();
    const t1 = performance.now();
    const midpointWall = wall0 + (t1 - t0) / 2;
    serverOffsetMs = data.server_epoch_ms - midpointWall;
  } catch (_) {
    serverOffsetMs = 0;
  }
}

export function serverEpochMs() {
  return Date.now() + serverOffsetMs;
}

export async function reportClientMetric(event, details = {}) {
  try {
    await fetch(`${TOKEN_SERVER}/client-metrics`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        event,
        // Do not confuse a monotonic value with an epoch timestamp. Both are sent.
        browser_epoch_ms: serverEpochMs(),
        browser_performance_ms: performance.now(),
        details,
      }),
      keepalive: true,
    });
  } catch (_) {
    // Telemetry must never interfere with playback.
  }
}
