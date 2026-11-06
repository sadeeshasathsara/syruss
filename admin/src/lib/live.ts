"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { LiveFact } from "./types";

export type LiveState = "connecting" | "live" | "paused" | "closed";

/**
 * Follows the platform's live view (sysadmin/live) over Server-Sent Events, through this app's signed-in proxy.
 * The server ends each stream after a few minutes and the browser opens a new one, so the sign-in stays fresh;
 * `onEvent` sees every event, the snapshot first. `paused` stops listening without forgetting what was shown.
 */
export function useLive(params: Record<string, string | undefined>, onEvent: (e: LiveFact) => void, paused = false) {
  const [state, setState] = useState<LiveState>("connecting");
  const [missed, setMissed] = useState(0);
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  const key = JSON.stringify(params);

  useEffect(() => {
    if (paused) return;
    let source: EventSource | null = null;
    let stopped = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const open = () => {
      source = new EventSource(api.liveUrl(JSON.parse(key)));
      source.onopen = () => setState("live");
      source.onmessage = (message) => {
        let event: LiveFact;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.kind === "reconnect") {
          source?.close();
          open();
          return;
        }
        if (event.kind === "closed") {
          source?.close();
          setState("closed");
          return;
        }
        if (event.kind === "missed") setMissed((n) => n + (event.count ?? 0));
        handler.current(event);
      };
      source.onerror = () => {
        // The browser retries by itself while the server says so; a stream refused outright (403) is closed for good.
        if (source?.readyState === EventSource.CLOSED && !stopped) {
          setState("connecting");
          retry = setTimeout(open, 5000);
        } else setState("connecting");
      };
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(retry);
      source?.close();
    };
  }, [key, paused]);

  return { state: paused ? ("paused" as const) : state, missed };
}
