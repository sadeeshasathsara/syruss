"use client";

import { useActionState } from "react";
import { accept, type AcceptState } from "./actions";

export function AcceptButton({ token, kind }: { token: string; kind: "workspace" | "organization" }) {
  const [state, action, pending] = useActionState(accept.bind(null, token, kind), {} as AcceptState);
  return (
    <form action={action}>
      {state.error ? <p className="form-error" role="alert">{state.error}</p> : null}
      <button className="button" type="submit" disabled={pending}>{pending ? "Joining…" : "Accept invitation"}</button>
    </form>
  );
}
