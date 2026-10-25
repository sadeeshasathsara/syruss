"use client";

import { ArrowUp, Check } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Photo } from "./Photo";
import { ASKS } from "@/lib/samples";

/** What the chat shows at one moment: how much of the question is typed, how far Syruss has got, how much of the answer is out. */
type View = { typed: number; sent: boolean; lines: number; done: boolean; answer: boolean; rows: number };

const EMPTY: View = { typed: 0, sent: false, lines: 0, done: false, answer: false, rows: 0 };

// The pace of one question, in milliseconds.
const START = 400;
const PER_LETTER = 28;
const AFTER_TYPING = 350;
const FIRST_LINE = 450;
const PER_LINE = 600;
const BEFORE_ANSWER = 650;
const PER_ROW = 240;
const HOLD = 5000;

/** The finished chat for one question: what stays on screen with reduced motion, or without script. */
function whole(q: number): View {
  const a = ASKS[q];
  return { typed: a.question.length, sent: true, lines: a.thinking.length, done: true, answer: true, rows: a.rows.length };
}

/** Every step of one question as (when, what the chat looks like then), and how long it all takes. */
function script(q: number): { steps: [number, View][]; length: number } {
  const a = ASKS[q];
  const steps: [number, View][] = [[0, EMPTY]];
  let t = START;
  for (let i = 1; i <= a.question.length; i++) steps.push([t + i * PER_LETTER, { ...EMPTY, typed: i }]);
  t += a.question.length * PER_LETTER + AFTER_TYPING;
  steps.push([t, { ...EMPTY, typed: a.question.length, sent: true }]);
  t += FIRST_LINE;
  for (let n = 1; n <= a.thinking.length; n++) {
    steps.push([t, { ...EMPTY, typed: a.question.length, sent: true, lines: n }]);
    t += PER_LINE;
  }
  t += BEFORE_ANSWER - PER_LINE;
  const base = { typed: a.question.length, sent: true, lines: a.thinking.length, done: true, answer: true };
  steps.push([t, { ...base, rows: 0 }]);
  for (let n = 1; n <= a.rows.length; n++) steps.push([t + n * PER_ROW, { ...base, rows: n }]);
  return { steps, length: t + a.rows.length * PER_ROW + HOLD };
}

const noop = () => () => {};
const motionAllowed = () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Ask Syruss: when it comes into view the chat plays by itself. A question is typed quickly and sent, Syruss shows what
 * it is looking at, and the answer arrives with the documents it came from. Then the next question. Choosing one
 * plays that one. With reduced motion, or without script, the first answer is simply shown.
 */
export function AskDemo() {
  const [q, setQ] = useState(0);
  const [round, setRound] = useState(0);
  const [view, setView] = useState<View>(() => whole(0));
  const [visible, setVisible] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const motion = useSyncExternalStore(noop, motionAllowed, () => false);
  const ask = ASKS[q];

  useEffect(() => {
    if (!stage.current) return;
    const watcher = new IntersectionObserver((hits) => setVisible(hits[0]?.isIntersecting ?? false), { threshold: 0.4 });
    watcher.observe(stage.current);
    return () => watcher.disconnect();
  }, []);

  useEffect(() => {
    if (!motion || !visible) return;
    const { steps, length } = script(q);
    const timers = steps.map(([at, v]) => setTimeout(() => setView(v), at));
    timers.push(setTimeout(() => setQ((q + 1) % ASKS.length), length));
    return () => timers.forEach(clearTimeout);
  }, [motion, visible, q, round]);

  const choose = (n: number) => {
    if (!motion) setView(whole(n));
    setQ(n);
    setRound((r) => r + 1);
  };

  return (
    <section className="ask" id="ask" aria-labelledby="ask-h">
      <div className="ask-stage" ref={stage}>
        <div className="ask-backdrop" aria-hidden="true">
          <Photo name="catalogue-drawers" fill sizes="100vw" className="ask-backdrop-img" />
        </div>
        <div className="ask-text">
          <h2 id="ask-h">Just ask. Skip the task.</h2>
          <p>Type a question the way you would ask a colleague. Syruss looks through all your documents and answers.</p>
          <div className="ask-questions" role="group" aria-label="Try a question">
            {ASKS.map((a, n) => (
              <button key={a.question} type="button" aria-pressed={n === q} onClick={() => choose(n)}>{a.question}</button>
            ))}
          </div>
          <p className="ask-note">Your questions are private to you. Nobody else in the workspace can read them.</p>
        </div>
        <div className="ask-chat" data-ui>
          <div className="ask-thread" aria-live="off">
            {view.sent ? <p className="ask-me" key={`me${q}`}>{ask.question}</p> : null}
            {view.lines > 0 ? (
              <div className="ask-think" key={`think${q}`}>
                <p className="ask-think-head">{view.done ? "Looked through your documents" : "Looking through your documents"}</p>
                <ul>
                  {ask.thinking.slice(0, view.lines).map((line, n) => {
                    const finished = view.done || n < view.lines - 1;
                    return (
                      <li key={line} data-state={finished ? "done" : "now"}>
                        <span className="ask-mark" aria-hidden="true">{finished ? <Check size={13} strokeWidth={3} /> : <span className="ask-spin" />}</span>
                        {line}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
            {view.answer ? (
              <div className="ask-answer" key={`answer${q}`}>
                <p>{ask.answer}</p>
                <table className="ask-table">
                  <thead><tr>{ask.head.map((h, n) => <th key={h} className={n === ask.head.length - 1 ? "r" : undefined}>{h}</th>)}</tr></thead>
                  <tbody>
                    {ask.rows.slice(0, view.rows).map((r) => (
                      <tr key={r.cells.join("|")}>
                        {r.cells.map((c, n) => <td key={n} className={n === r.cells.length - 1 ? "r" : undefined}>{c}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
          <div className="ask-composer" aria-hidden="true">
            <span className="ask-typed" data-empty={view.sent || view.typed === 0}>
              {view.sent || view.typed === 0 ? "Ask about your documents" : ask.question.slice(0, view.typed)}
              {!view.sent && view.typed > 0 ? <i className="ask-caret" /> : null}
            </span>
            <span className="ask-send"><ArrowUp size={16} strokeWidth={2.5} /></span>
          </div>
        </div>
      </div>
    </section>
  );
}
