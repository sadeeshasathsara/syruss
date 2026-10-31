"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { NoAccess, Section, SettingsHeader } from "@/components/settings/kit";
import { Pill } from "@/components/ui/Pill";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { TYPE_LABEL } from "./health";

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="scrollbar-thin overflow-x-auto rounded-card border border-border bg-card">
      <table className="w-full min-w-[480px] text-left text-body">
        <thead>
          <tr className="border-b border-border text-meta text-muted">
            {head.map((h) => <th key={h} scope="col" className="px-4 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-rule align-top last:border-0">
              {r.map((cell, j) => <td key={j} className={j === 0 ? "whitespace-nowrap px-4 py-2 font-mono text-meta text-ink" : "px-4 py-2 text-ink"}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const STATUSES: [string, string][] = [
  ["received", "Stored and waiting for its turn."],
  ["processing", "Being read right now."],
  ["needs_attention", "A person is looking at it: Syruss had a doubt and asked, or a reviewer has it."],
  ["approved", "Read and approved. The record is in the answer."],
  ["failed", "It could not be read. The answer says why."],
];

const ERRORS: [string, string, string][] = [
  ["400", "invalid_file", "The file is empty."],
  ["401", "unauthorized", "The key is missing, wrong, expired or revoked."],
  ["402", "plan_limit_reached", "The workspace’s plan allowance for the month is used up."],
  ["403", "forbidden", "The key can’t send here: another kind of key, a paused channel (channel_paused), or an address that isn’t allowed (ip_not_allowed)."],
  ["404", "not_found", "No such document in this channel."],
  ["409", "conflict", "Your own reference (external_id) was already used for a different file, or the workspace has no published template yet."],
  ["410", "expired", "The upload link has expired."],
  ["413", "file_too_large", "The file or picture is bigger than the channel allows."],
  ["415", "unsupported_type", "The file isn’t one of the accepted image types. What the file really is counts, not its name."],
  ["422", "invalid_request", "A field is malformed: external_id, metadata, or an Idempotency-Key reused with a different file."],
  ["429", "rate_limited · quota_exceeded", "Too many requests a minute, or the key’s daily limit. Retry-After says when to try again."],
];

/** Everything a developer needs to connect a system, with the real address of this API. */
export function DevelopersPage() {
  const { workspace, href } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const allowed = can("integrations.manage");
  const info = useQuery({ queryKey: ["channels", workspace.id, "developer-info"], queryFn: () => api.channels.developerInfo(workspace.id), enabled: allowed });
  const title = "Developers";
  const description = "Connect a system that sends documents to Syruss and receives the approved data.";
  if (!allowed) return <div className="flex flex-col"><SettingsHeader title={title} description={description} /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;

  const base = info.data?.base_url ?? "https://api.example.com/api/v1";
  const intake = `${base}/intake`;
  const accepts = (info.data?.accepts ?? []).map((t) => TYPE_LABEL[t] ?? t).join(", ") || "JPEG, PNG, TIFF, WebP";

  const send = [
    { label: "curl", code: `curl -X POST "${intake}/documents" \\\n  -H "Authorization: Bearer $SYRUSS_KEY" \\\n  -H "Idempotency-Key: invoice-1001" \\\n  -F "file=@invoice.png" \\\n  -F "external_id=INV-1001" \\\n  -F 'metadata={"branch":"North","currency":"EUR"}'` },
    { label: "Python", code: `import os, requests\n\nres = requests.post(\n    "${intake}/documents",\n    headers={\n        "Authorization": f"Bearer {os.environ['SYRUSS_KEY']}",\n        "Idempotency-Key": "invoice-1001",   # the same value on a retry: no second document\n    },\n    files={"file": open("invoice.png", "rb")},\n    data={"external_id": "INV-1001", "metadata": '{"branch": "North"}'},\n    timeout=30,\n)\nres.raise_for_status()\nprint(res.json()["status_url"])` },
    { label: "Node", code: `import { readFile } from "node:fs/promises";\n\nconst form = new FormData();\nform.append("file", new Blob([await readFile("invoice.png")], { type: "image/png" }), "invoice.png");\nform.append("external_id", "INV-1001");\nform.append("metadata", JSON.stringify({ branch: "North" }));\n\nconst res = await fetch("${intake}/documents", {\n  method: "POST",\n  headers: { Authorization: \`Bearer \${process.env.SYRUSS_KEY}\`, "Idempotency-Key": "invoice-1001" },\n  body: form,\n});\nif (!res.ok) throw new Error(JSON.stringify(await res.json()));\nconsole.log((await res.json()).status_url);` },
  ];
  const accepted = `{\n  "id": "9b2f0c1e-6a1d-4d6a-a3f4-2c8f6f1f9a10",\n  "status": "received",\n  "external_id": "INV-1001",\n  "filename": "invoice.png",\n  "metadata": { "branch": "North", "currency": "EUR" },\n  "received_at": "2026-10-25T09:14:03Z",\n  "test": false,\n  "status_url": "${intake}/documents/9b2f0c1e-6a1d-4d6a-a3f4-2c8f6f1f9a10"\n}`;
  const approved = `{\n  "id": "9b2f0c1e-…",\n  "status": "approved",\n  "external_id": "INV-1001",\n  "record": {\n    "data": { "Invoice": [{ "number": "INV-1001", "total": 1280.5 }] },\n    "confidence": 0.97,\n    "approved_at": "2026-10-25T09:14:41Z"\n  }\n}`;
  const status = [
    { label: "curl", code: `curl "${intake}/documents/$DOCUMENT_ID" -H "Authorization: Bearer $SYRUSS_KEY"\n\n# or find it by your own reference\ncurl "${intake}/documents?external_id=INV-1001" -H "Authorization: Bearer $SYRUSS_KEY"` },
    { label: "Python", code: `doc = requests.get(\n    "${intake}/documents",\n    params={"external_id": "INV-1001"},\n    headers={"Authorization": f"Bearer {os.environ['SYRUSS_KEY']}"},\n).json()["items"][0]\nprint(doc["status"])` },
  ];
  const big = [
    { label: "curl", code: `# 1. Ask for an upload link (JSON). size_bytes must be the file's exact size.\ncurl -X POST "${intake}/uploads" \\\n  -H "Authorization: Bearer $SYRUSS_KEY" -H "Content-Type: application/json" \\\n  -d '{"filename":"scan.tiff","content_type":"image/tiff","size_bytes":48211093,"external_id":"SCAN-42"}'\n\n# 2. Send the file straight to storage: every returned field, then the file last, named "file".\ncurl -X POST "$UPLOAD_URL" -F key=… -F Content-Type=image/tiff -F policy=… (the other fields) -F "file=@scan.tiff"\n\n# 3. Say it arrived. It is checked as strictly as a direct upload, then queued.\ncurl -X POST "${intake}/uploads/$UPLOAD_ID/complete" -H "Authorization: Bearer $SYRUSS_KEY"` },
    { label: "Python", code: `up = requests.post(\n    "${intake}/uploads", headers=auth,\n    json={"filename": "scan.tiff", "content_type": "image/tiff", "size_bytes": os.path.getsize("scan.tiff")},\n).json()\n\nwith open("scan.tiff", "rb") as f:\n    stored = requests.post(up["url"], data=up["fields"], files={"file": f})\nstored.raise_for_status()\n\ndoc = requests.post(up["complete_url"], headers=auth).json()` },
  ];
  const message = `{\n  "id": "d1c0…",                       // this delivery\n  "type": "document.completed",       // or document.corrected\n  "created_at": "2026-10-25T09:15:02Z",\n  "test": false,\n  "workspace": { "id": "…", "name": "${info.data?.workspace ?? "Supplier invoices"}" },\n  "document": { "id": "9b2f…", "filename": "invoice.png", "external_id": "INV-1001", "metadata": { "branch": "North" } },\n  "record": {\n    "revision": 1,\n    "mode": "record",                 // or "mapped" when the destination reshapes it\n    "confidence": 0.97,\n    "approved_at": "2026-10-25T09:14:41Z",\n    "data": { "Invoice": [{ "number": "INV-1001", "total": 1280.5 }] }\n  }\n}`;
  const verify = [
    { label: "Python", code: `import hashlib, hmac, time\n\ndef verify(raw_body: bytes, headers: dict, secret: str) -> bool:\n    stamp = headers["X-Syruss-Timestamp"]\n    if abs(time.time() - int(stamp)) > 300:\n        return False                      # too old: could be a replay\n    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + raw_body, hashlib.sha256).hexdigest()\n    sent = [p.removeprefix("v1=") for p in headers["X-Syruss-Signature"].split(",")]\n    return any(hmac.compare_digest(expected, s) for s in sent)` },
    { label: "Node", code: `import { createHmac, timingSafeEqual } from "node:crypto";\n\nexport function verify(rawBody, headers, secret) {\n  const stamp = headers["x-syruss-timestamp"];\n  if (Math.abs(Date.now() / 1000 - Number(stamp)) > 300) return false;   // too old\n  const expected = createHmac("sha256", secret).update(\`\${stamp}.\`).update(rawBody).digest("hex");\n  return headers["x-syruss-signature"].split(",").some((part) => {\n    const sent = Buffer.from(part.replace("v1=", ""), "hex");\n    return sent.length === expected.length / 2 && timingSafeEqual(sent, Buffer.from(expected, "hex"));\n  });\n}` },
  ];
  const quickstart = `export SYRUSS_KEY="sk_live_…"   # from a channel's Keys\n\n# Check the key and see what the channel accepts\ncurl "${intake}/me" -H "Authorization: Bearer $SYRUSS_KEY"`;

  return (
    <div className="flex flex-col">
      <SettingsHeader title={title} description={description} />

      <Section title="Quickstart" description="Three steps from nothing to a document arriving.">
        <ol className="flex flex-col gap-4">
          {[
            [<>Create a <Link className="underline" href={href("connections/receive")}>channel</Link> and a key.</>, "The key is shown once. Start with a test key: its documents are read but never published."],
            [<>Check the key.</>, "It answers with the channel, its mode and what it accepts."],
            [<>Send a file.</>, "You get an answer straight away with a link to follow it. Reading happens afterwards."],
          ].map(([head, tail], i) => (
            <li key={i} className="flex gap-3">
              <span className="num flex size-6 flex-none items-center justify-center rounded-full bg-active text-meta font-medium text-ink">{i + 1}</span>
              <span className="min-w-0"><span className="block text-body font-medium text-ink">{head}</span><span className="block text-body text-muted">{tail}</span></span>
            </li>
          ))}
        </ol>
        <div className="mt-4"><CodeBlock samples={[{ label: "Shell", code: quickstart }]} /></div>
      </Section>

      <Section title="Authentication" description="The key says who you are and where your documents go. You never name the organization or workspace.">
        <div className="flex flex-col gap-3 text-body text-ink">
          <p>Send the key as <code className="font-mono text-meta">Authorization: Bearer sk_live_…</code> (or <code className="font-mono text-meta">X-API-Key</code>). Always over HTTPS.</p>
          <p className="text-muted">A key belongs to one channel. It can send documents and read back the ones it sent, and nothing else: on any other part of the API it is refused. Keep it on your server, never in a browser or an app.</p>
        </div>
      </Section>

      <Section title="Send a document" description={`POST ${intake}/documents, as multipart form data. It answers 202 Accepted.`}>
        <div className="flex flex-col gap-4">
          <CodeBlock samples={send} />
          <Table head={["Field", "What it is"]} rows={[
            ["file", <>The document. Accepted: {accepts}. Up to {info.data?.max_file_mb ?? 10} MB (the channel may allow less).</>],
            ["external_id", "Your own reference for it, such as an invoice number. It comes back on every answer and lets you look the document up."],
            ["metadata", "A small JSON object of your own labels, kept with the document. Text, numbers and true/false; up to 20 entries."],
            ["process", "false stores the file without reading it yet. Default: the channel’s setting."],
            ["Idempotency-Key (header)", "Any unique value for this request. Send it again on a retry and you get the same document back (200, Idempotent-Replayed: true), never a second."],
          ]} />
          <CodeBlock samples={[{ label: "202 Accepted", code: accepted }]} />
        </div>
      </Section>

      <Section title="Follow a document" description="Poll the status link, or look it up by your own reference. When it is approved the answer carries the record.">
        <div className="flex flex-col gap-4">
          <CodeBlock samples={status} />
          <Table head={["status", "Meaning"]} rows={STATUSES.map(([s, d]) => [s, d])} />
          <CodeBlock samples={[{ label: "approved", code: approved }]} />
          <p className="text-body text-muted">A document that needs a person is held until it is approved. Nothing is published from it before that, and a correction after approval is sent on as an update.</p>
        </div>
      </Section>

      <Section title="Big files" description={`Files too big to send through the API go straight to storage. The link works once, for ${info.data?.upload_minutes ?? 15} minutes, for exactly one file of the size you announced.`}>
        <CodeBlock samples={big} />
      </Section>

      <Section title="Errors" description="Every error has the same shape, with a code to program against and a sentence to show a person.">
        <div className="flex flex-col gap-4">
          <CodeBlock samples={[{ label: "415", code: `{\n  "detail": {\n    "code": "unsupported_type",\n    "message": "This is a PDF. Send a JPG, PNG, TIFF or WebP image."\n  }\n}` }]} />
          <Table head={["Status", "Code", "Meaning"]} rows={ERRORS.map(([s, c, m]) => [s, <code key={c} className="font-mono text-meta">{c}</code>, m])} />
          <p className="text-body text-muted">Retry 429 and 5xx with a pause, using the same Idempotency-Key. Don’t retry 4xx without changing something.</p>
        </div>
      </Section>

      <Section title="Receive approved records" description="Connect a webhook or REST destination and every approved record is sent to your system. Nothing is sent before a document is approved, or while anything is open about it.">
        <div className="flex flex-col gap-4">
          <CodeBlock samples={[{ label: "The message", code: message }]} />
          <Table head={["Header", "What it is"]} rows={[
            ["X-Syruss-Signature", <>Proof it came from Syruss: <code className="font-mono text-meta">v1=&lt;hex&gt;</code>, an HMAC-SHA256 of <code className="font-mono text-meta">timestamp + &quot;.&quot; + raw body</code> with your signing secret. It can hold two (<code className="font-mono text-meta">v1=…,v1=…</code>) while a secret is being replaced.</>],
            ["X-Syruss-Timestamp", "When it was sent, in seconds. Refuse anything older than five minutes."],
            ["Idempotency-Key", "The same on every try of the same delivery. Keep the ones you have handled, and answer 200 to a repeat without doing the work again."],
            ["X-Syruss-Delivery · X-Syruss-Event", "The delivery’s id, and document.completed for a first approval or document.corrected for a correction (the revision rises)."],
          ]} />
          <CodeBlock samples={verify} />
          <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body text-ink">
            <li>Answer with any 2xx within 20 seconds to say you have it. Anything else is tried again after 1 minute, 5 minutes, 30 minutes, 2 hours, 6, 12 and 24 hours, then set aside as failed so a person can replay it.</li>
            <li>A 4xx answer (other than 408 and 429) is not retried: it means the message itself was refused, and shows as failed straight away.</li>
            <li>A destination that fails ten times in a row is paused, and the people who run it are told. What is waiting is kept.</li>
          </ul>
        </div>
      </Section>

      <Section title="Test before going live" description="A test key behaves like a live one but its documents are only trials.">
        <ul className="flex flex-col gap-2 text-body text-ink">
          <li className="flex gap-2"><Pill tone="info">Test</Pill><span>Keys that start <code className="font-mono text-meta">sk_test_</code>. Documents are read by the agent, marked as tests, left out of your plan’s count, and never published to a destination.</span></li>
          <li className="flex gap-2"><Pill tone="neutral">Live</Pill><span>Keys that start <code className="font-mono text-meta">sk_live_</code>. Use one when your side works.</span></li>
        </ul>
      </Section>

      <Section title="Good to know">
        <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body text-ink">
          <li>Each key has its own limits: requests a minute, documents a day, and optionally the addresses it may be used from. They’re set when you create it.</li>
          <li>Rotate a key without a gap: create the replacement, keep the old one running for a while, switch the sender, then let the old one lapse.</li>
          <li>Every attempt, accepted or refused, appears in the channel’s activity with the reason.</li>
        </ul>
      </Section>
    </div>
  );
}
