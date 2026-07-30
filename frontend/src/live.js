// The live feed: one EventSource per open group, replacing the 5s poll.
//
// The server sends pokes, never state (see backend/app/realtime/bus.py) — a
// frame says "plans changed in group 4", and this hook refetches through the
// normal authenticated endpoint. So nothing here has to know who is allowed to
// see what; that stays the API's job.
//
// The poll doesn't go away, it changes cadence. While the stream is up it drops
// to a slow backstop (a missed poke should cost seconds of staleness, never
// correctness — and a multi-worker deployment would only reach the workers a
// client is connected to). When the stream is down — no EventSource, a proxy
// that eats text/event-stream, the server at capacity — it goes back to the
// old 5s cadence and the app behaves exactly as it did before.
import { useEffect, useRef, useState } from "react";

export const LIVE_BACKSTOP_MS = 60000; // stream up: paranoia only
export const POLL_MS = 5000; // stream down: the pre-SSE cadence

export function useGroupLive({ groupId, onPlans, onEvents }) {
  const [live, setLive] = useState(false);
  // handlers are re-created on every render (they close over the group id);
  // going through a ref keeps that from tearing down the connection each time
  const handlers = useRef({ onPlans, onEvents });
  handlers.current = { onPlans, onEvents };

  useEffect(() => {
    if (!groupId || typeof EventSource === "undefined") return;
    const es = new EventSource(`/groups/${groupId}/stream`);

    es.addEventListener("hello", () => {
      // Also fires on every automatic reconnect, which is exactly when we may
      // have missed something: resync both lists rather than trusting that
      // nothing moved while the connection was down.
      setLive(true);
      handlers.current.onPlans?.();
      handlers.current.onEvents?.();
    });
    es.addEventListener("plans", () => handlers.current.onPlans?.());
    es.addEventListener("events", () => handlers.current.onEvents?.());
    // A dropped connection is retried by the browser on its own; an HTTP error
    // (401 after a session expires, 503 at capacity) closes it for good. Either
    // way the poll is what carries the app until a frame arrives again.
    es.onerror = () => setLive(false);

    return () => {
      es.close();
      setLive(false);
    };
  }, [groupId]);

  return live;
}
