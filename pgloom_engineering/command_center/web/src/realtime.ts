import { refreshForEvent, type CCEvent } from "./api";

export type RealtimeConnectionState = {
  state: "connecting" | "connected" | "reconnecting" | "disconnected";
  attempt: number;
  lastEventAt?: string;
  nextRetryMs?: number;
};

export function connectRealtime(
  onEvent: (event: CCEvent) => void,
  onStatus?: (status: RealtimeConnectionState) => void
) {
  let closed = false;
  let socket: WebSocket | null = null;
  let timer: number | null = null;
  let attempt = 0;

  const open = () => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    onStatus?.({ state: attempt === 0 ? "connecting" : "reconnecting", attempt });
    socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
    socket.onopen = () => {
      if (attempt > 0) {
        refreshForEvent({ kind: "resync", reason: "websocket reconnect" });
      }
      onStatus?.({ state: "connected", attempt });
      attempt = 0;
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as CCEvent;
      refreshForEvent(event);
      onStatus?.({ state: "connected", attempt, lastEventAt: event.ts || new Date().toISOString() });
      onEvent(event);
    };
    socket.onclose = () => {
      if (!closed) {
        attempt += 1;
        const nextRetryMs = Math.min(10_000, 1000 * 2 ** Math.min(attempt, 4));
        refreshForEvent({ kind: "resync", reason: "websocket closed" });
        onStatus?.({ state: "reconnecting", attempt, nextRetryMs });
        timer = window.setTimeout(open, nextRetryMs);
      }
    };
    socket.onerror = () => {
      onStatus?.({ state: "reconnecting", attempt: attempt + 1 });
    };
  };

  open();
  return () => {
    closed = true;
    if (timer != null) window.clearTimeout(timer);
    socket?.close();
    onStatus?.({ state: "disconnected", attempt });
  };
}
