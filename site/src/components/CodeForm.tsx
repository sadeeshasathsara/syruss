"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import type { FormState } from "@/app/onboarding/actions";

export type CodeStatus = { code_sent: boolean; resend_in: number; code_valid_minutes: number };

const initial: FormState = {};

/**
 * Asks for a 6-digit code that was emailed. A code is sent as soon as this opens (unless one is already waiting),
 * the form submits by itself when all six digits are in, and a new one can be asked for once the wait is over.
 */
export function CodeForm({ confirm, send, start, sentTo, hidden, submitLabel, remember }: {
  confirm: (state: FormState, data: FormData) => Promise<FormState>;
  send: () => Promise<{ status?: CodeStatus; error?: string }>;
  start: CodeStatus;
  sentTo: string;
  hidden?: Record<string, string | undefined>;
  submitLabel: string;
  /** Shows a tick box ("Trust this browser…"), on by default. */
  remember?: string;
}) {
  const [state, action, pending] = useActionState(confirm, initial);
  const [status, setStatus] = useState(start);
  const [notice, setNotice] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [wait, setWait] = useState(start.resend_in);
  const [sending, setSending] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const asked = useRef(false);

  const resend = async () => {
    setSending(true);
    setNotice(null);
    const res = await send();
    if (res.status) {
      setStatus(res.status);
      setWait(res.status.resend_in);
    }
    setNotice(res.error ?? "A new code is on its way.");
    setSending(false);
  };

  // Ask for the first code when the step opens.
  useEffect(() => {
    if (asked.current || start.code_sent) return;
    asked.current = true;
    void resend();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (wait <= 0) return;
    const timer = setInterval(() => setWait((w) => Math.max(0, w - 1)), 1000);
    return () => clearInterval(timer);
  }, [wait > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <form action={action} ref={form} noValidate>
      {Object.entries(hidden ?? {}).map(([name, value]) => (value ? <input key={name} type="hidden" name={name} value={value} /> : null))}
      <div className="field">
        <label htmlFor="code">Confirmation code</label>
        <input
          id="code" name="code" className="code-input" inputMode="numeric" autoComplete="one-time-code" autoFocus
          maxLength={9} placeholder="······" value={code} aria-invalid={!!state.error}
          aria-describedby={state.error ? "code-error" : "code-hint"}
          onChange={(e) => {
            const digits = e.target.value.replace(/\D/g, "").slice(0, 6);
            setCode(digits);
            if (digits.length === 6) setTimeout(() => form.current?.requestSubmit(), 120);
          }}
        />
        {state.error ? (
          <span className="error" id="code-error" role="alert">{state.error}</span>
        ) : (
          <span className="hint" id="code-hint">
            {status.code_sent ? `Sent to ${sentTo}. It works for ${status.code_valid_minutes} minutes.` : sending ? "Sending your code…" : `We'll email a code to ${sentTo}.`}
          </span>
        )}
      </div>
      {remember ? (
        <label className="check">
          <input type="checkbox" name="remember" defaultChecked />
          <span>{remember}</span>
        </label>
      ) : null}
      <button className="button" type="submit" disabled={pending || code.length < 6}>{pending ? "Checking…" : submitLabel}</button>
      <p className="resend">
        {notice ? <span role="status">{notice} </span> : null}
        <button type="button" className="link-button" disabled={sending || wait > 0} onClick={() => void resend()}>
          {wait > 0 ? `Send a new code in ${wait}s` : "Send a new code"}
        </button>
      </p>
    </form>
  );
}
