import { useEffect, useMemo, useState } from "react";

const TOKEN_SERVER = import.meta.env.VITE_TOKEN_SERVER_URL || "http://localhost:8000";

function ms(value) {
  return value == null ? "—" : `${(value * 1000).toFixed(0)} ms`;
}

export default function StatusBoard() {
  const [events, setEvents] = useState([]);

  useEffect(() => {
    const refresh = () => fetch(`${TOKEN_SERVER}/metrics/latest?n=100`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setEvents(d.events || []))
      .catch(() => {});
    refresh();
    const id = setInterval(refresh, 1000);
    return () => clearInterval(id);
  }, []);

const latest = useMemo(() => {
  // Select one completed/current turn first; never compose a row from unrelated turns.
  const ordered = [...events].reverse();

  const selectedTurn = ordered.find((e) =>
    [
      "client_playback_started",
      "rime_first_byte",
      "speech_stopped",
      "recovery_success",
      "stt_finalize",
    ].includes(e.event)
  )?.turn_id;

  const turnEvents = selectedTurn
    ? events.filter((e) => e.turn_id === selectedTurn)
    : [];

  const find = (name) =>
    [...turnEvents].reverse().find((e) => e.event === name);

  const stt = find("stt_finalize");
  const playback = find("client_playback_started");

  const eou = stt?.end_of_utterance_delay;

  // Both timestamps are server epoch seconds.
  // Convert the difference to milliseconds.
  const endToEnd =
    playback?.timestamp != null && stt?.timestamp != null
      ? (playback.timestamp - stt.timestamp) * 1000
      : null;

  return {
    turnId: selectedTurn,
    eou,
    ttft: find("llm_first_token")?.ttft,
    ttfb: find("rime_first_byte")?.ttfb,
    playback,
    endToEnd,
    interrupted: Boolean(find("barge_in_detected")),
    stopped: Boolean(
      find("client_playback_stopped") || find("speech_cancelled")
    ),
    stale: Boolean(find("stale_result_discarded")),
    recovered: Boolean(find("recovery_success")),
  };
}, [events]);

  return (
    <section className="status-board">
      <div className="board-title">VOICE PERFORMANCE {latest.turnId ? `· TURN ${latest.turnId.slice(0, 8)}` : ""}</div>
      <div className="metric-grid">
        <div><span>EOU</span><strong>{ms(latest.eou)}</strong></div>
        <div><span>LLM TTFT</span><strong>{ms(latest.ttft)}</strong></div>
        <div><span>Rime TTFB</span><strong>{ms(latest.ttfb)}</strong></div>
        <div><span>Playback</span><strong>{latest.playback ? "started" : "—"}</strong></div>
        <div><span>End-to-end</span><strong>{latest.endToEnd == null ? "—" : `${latest.endToEnd.toFixed(0)} ms`}</strong></div>
      </div>
      <div className="recovery-row">
        <span>Interruption <b>{latest.interrupted ? "✓" : "—"}</b></span>
        <span>Playback stopped <b>{latest.stopped ? "✓" : "—"}</b></span>
        <span>Stale result <b>{latest.stale ? "discarded" : "—"}</b></span>
        <span>Recovery <b>{latest.recovered ? "✓" : "—"}</b></span>
      </div>
    </section>
  );
}
