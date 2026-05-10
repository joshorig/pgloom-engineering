import { refreshForEvent, type CCEvent } from "./api";

export function connectRealtime(onEvent: (event: CCEvent) => void) {
  let closed = false;
  let socket: WebSocket | null = null;
  let timer: number | null = null;

  const open = () => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as CCEvent;
      refreshForEvent(event);
      onEvent(event);
    };
    socket.onclose = () => {
      if (!closed) timer = window.setTimeout(open, 1500);
    };
  };

  open();
  return () => {
    closed = true;
    if (timer != null) window.clearTimeout(timer);
    socket?.close();
  };
}
