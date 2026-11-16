"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { loadPage } from "@/lib/browser";

/**
 * Picking up where someone left off after they have to sign in again (their session ended, or an action asks for a fresh sign-in).
 *
 *   usePreservedState(key, initial)   a drop-in for useState on a form's fields (and on whether its dialog is open). When the app is
 *                                     about to send the person to sign in, every one of these is collected; when they come back
 *                                     to the same page, the dialog and the fields are as they left them.
 *   resumeAndGo(address)              what the app does instead of leaving at once: it hands the collected state to the server to
 *                                     seal (app/api/drafts), keeps only the sealed blob in this tab (sessionStorage), and goes.
 *
 * What is kept is never readable here: the browser only ever holds the server's sealed blob, tied to the person and the page, and good
 * for 15 minutes; a form's passwords, keys and tokens are never put in it (keep them in plain useState, and the server drops any key that
 * looks like one). The browser can't be trusted with the draft, so it is only a courier.
 */
const KEY = "syruss.resume";
const registry = new Map<string, () => unknown>();
let restored: Record<string, unknown> = {};
const listeners = new Set<() => void>();
const here = () => window.location.pathname + window.location.search;

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/**
 * `omit`: names of an object state's fields that are never kept (a form's passwords, keys, tokens). They are left out before anything
 * leaves this page, and come back as they were at the start.
 */
export function usePreservedState<T>(key: string, initial: T, omit: string[] = []): [T, Dispatch<SetStateAction<T>>] {
  const back = (v: unknown): T => (isObject(initial) && isObject(v) ? ({ ...initial, ...v } as T) : (v as T));
  // A form that opens after the draft came back starts from it; one that was already there takes it when it arrives.
  const [local, setLocal] = useState<{ v: T } | null>(() => (key in restored ? { v: back(restored[key]) } : null));
  const value = local ? local.v : initial;
  const latest = useRef(value);
  useEffect(() => {
    latest.current = value;
  });
  useEffect(() => {
    registry.set(key, () => {
      const v = latest.current as unknown;
      if (!omit.length || !isObject(v)) return v;
      return Object.fromEntries(Object.entries(v).filter(([name]) => !omit.includes(name)));
    });
    const adopt = () => {
      if (key in restored) setLocal((cur) => cur ?? { v: back(restored[key]) });
    };
    listeners.add(adopt);
    return () => {
      registry.delete(key);
      listeners.delete(adopt);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set: Dispatch<SetStateAction<T>> = useCallback((next) => {
    setLocal((cur) => ({ v: typeof next === "function" ? (next as (p: T) => T)(cur ? cur.v : initial) : next }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return [value, set];
}

async function post(body: unknown, ms = 4000): Promise<Response | null> {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), ms);
  try {
    return await fetch("/api/drafts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: stop.signal });
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Seals what is in progress on this page, once. Best effort: if it can't be done, signing in still goes ahead. */
export async function saveDrafts(): Promise<void> {
  const data: Record<string, unknown> = {};
  registry.forEach((get, key) => {
    data[key] = get();
  });
  try {
    sessionStorage.removeItem(KEY);
    if (!Object.keys(data).length) return;
    const res = await post({ op: "save", path: here(), data });
    const blob = res?.ok ? ((await res.json()) as { blob?: string }).blob : null;
    if (blob) sessionStorage.setItem(KEY, blob);
  } catch {
    /* nothing kept: the person starts the form again */
  }
}

/** Goes to sign in (or anywhere that brings the person back) with what they were doing kept. Never returns: the page is left. */
let leaving: Promise<never> | null = null;
export function resumeAndGo(address: string): Promise<never> {
  // Several requests can find the session gone at once: it is saved and left once.
  leaving ??= (async () => {
    await saveDrafts();
    loadPage(address);
    return new Promise<never>(() => {});
  })();
  return leaving;
}

let booted = false;

/** On every page: if a sealed draft was left for this page, asks the server to open it (once) and hands it to the forms. */
export function ResumeBoot() {
  useEffect(() => {
    // Once per page load, even where React runs an effect twice: the draft is used up the first time.
    if (booted) return;
    booted = true;
    let blob: string | null = null;
    try {
      blob = sessionStorage.getItem(KEY);
      sessionStorage.removeItem(KEY);
    } catch {
      return;
    }
    if (!blob) return;
    (async () => {
      const res = await post({ op: "restore", path: here(), blob });
      const data = res?.ok ? ((await res.json()) as { data?: Record<string, unknown> | null }).data : null;
      if (!data) return;
      restored = data;
      listeners.forEach((l) => l());
      setTimeout(() => {
        restored = {};
      }, 30_000); // long enough for every form on the page to take its part
    })();
  }, []);
  return null;
}
