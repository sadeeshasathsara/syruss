/**
 * The short chime for a new notification, made with the browser's own audio (no file to load). Browsers keep audio
 * silent until the person has clicked or typed on the page once, so the first interaction wakes it up. The choice to
 * hear it or not belongs to the device, not the account.
 */
import { useSyncExternalStore } from "react";

const KEY = "syruss.sound";
let context: AudioContext | null = null;
let armed = false;

const CHANGED = "syruss:sound";

/** Whether the chime is on, as a value that follows changes (and reads the device's choice only once on the client). */
export function useSoundOn(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      window.addEventListener(CHANGED, onChange);
      window.addEventListener("storage", onChange);
      return () => {
        window.removeEventListener(CHANGED, onChange);
        window.removeEventListener("storage", onChange);
      };
    },
    soundOn,
    () => true,
  );
}

export function soundOn(): boolean {
  try {
    return localStorage.getItem(KEY) !== "off";
  } catch {
    return true;
  }
}

export function setSound(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* the choice just won't be remembered */
  }
  window.dispatchEvent(new Event(CHANGED));
  if (on) chime(); // so they hear what they turned on
}

function audio(): AudioContext | null {
  try {
    context ??= new AudioContext();
    return context;
  } catch {
    return null;
  }
}

/** Call once when the app starts: the first click or key press lets the browser play sound. */
export function armSound(): void {
  if (armed || typeof document === "undefined") return;
  armed = true;
  const wake = () => void audio()?.resume();
  document.addEventListener("pointerdown", wake, { once: true });
  document.addEventListener("keydown", wake, { once: true });
}

export function chime(): void {
  const ctx = audio();
  if (!ctx || ctx.state !== "running") return;
  const start = ctx.currentTime + 0.01;
  // Two soft notes, a fifth apart, each fading out quickly.
  [[880, 0], [1318.5, 0.12]].forEach(([frequency, offset]) => {
    const tone = ctx.createOscillator();
    const gain = ctx.createGain();
    tone.type = "sine";
    tone.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start + offset);
    gain.gain.exponentialRampToValueAtTime(0.09, start + offset + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.5);
    tone.connect(gain).connect(ctx.destination);
    tone.start(start + offset);
    tone.stop(start + offset + 0.55);
  });
}

/** For a notification that has just arrived. */
export function playNotificationSound(): void {
  if (soundOn()) chime();
}
