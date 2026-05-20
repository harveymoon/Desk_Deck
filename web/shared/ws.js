// Tiny WebSocket client with reconnect.
export function connect({ url, onMessage, onOpen, onClose }) {
  let ws = null;
  let alive = true;
  let backoff = 500;

  function open() {
    ws = new WebSocket(url);
    ws.addEventListener("open", () => {
      backoff = 500;
      onOpen && onOpen();
    });
    ws.addEventListener("message", (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch (err) {
        console.error("bad ws msg", err, e.data);
      }
    });
    ws.addEventListener("close", () => {
      onClose && onClose();
      if (!alive) return;
      backoff = Math.min(backoff * 1.6, 5000);
      setTimeout(open, backoff);
    });
    ws.addEventListener("error", () => ws && ws.close());
  }

  open();

  return {
    send(msg) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
      }
    },
    close() {
      alive = false;
      ws && ws.close();
    },
  };
}
