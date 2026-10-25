"""
Live end-to-end run against the running services (core server + agent service + real model).

  1. Upload sample rosters -> the agent designs the template (proposal + questions)
  2. Accept the proposed template
  3. Upload documents -> the agent processes them
  4. Print what the agent did: records, reviews, questions, traces

Usage (services already running):
  python bridge/e2e_live.py --samples 2 --documents 2 [--start 4]
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path
import httpx

API = "http://localhost:8000/api/v1"
DATASET = Path(__file__).parent / "testing-dataset"


def wait_for(fn, timeout: float, every: float = 3.0, label: str = ""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(every)
    sys.exit(f"Timed out waiting for {label}")


def show(title: str, value) -> None:
    print(f"\n=== {title} ===")
    print(value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)[:6000])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--documents", type=int, default=2)
    parser.add_argument("--start", type=int, default=None, help="Dataset index of the first document (default: right after the samples)")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    images = sorted(DATASET.glob("*.jpeg"))
    start = args.samples if args.start is None else args.start
    samples, documents = images[: args.samples], images[start: start + args.documents]
    http = httpx.Client(timeout=120)

    # 1. Design the template from samples
    session = f"tpl_{uuid.uuid4().hex[:8]}"
    files = [("files", (p.name, p.read_bytes(), "image/jpeg")) for p in samples]
    res = http.post(f"{API}/templates/discover", files=files,
                    data={"session_id": session, "user_notes": "Daily attendance rosters from our security guard sites."})
    res.raise_for_status()
    tpl = res.json()["template_id"]
    h = {"X-Template-ID": tpl}
    print(f"Draft workspace {tpl}; waiting for the agent's template proposal...")

    def proposal():
        items = http.get(f"{API}/knowledge/proposals", headers=h, params={"status": "PENDING"}).json()
        return next((p for p in items if p["proposal_type"] == "TEMPLATE_CHANGE"), None)

    prop = wait_for(proposal, args.timeout, label="template proposal")
    show("Agent's observation", prop["observation"])
    show("Proposed schema", prop["proposed_data"]["schema_definition"])
    show("Agent's questions", http.get(f"{API}/questions", headers=h).json())

    # 2. Accept it
    schema = prop["proposed_data"]["schema_definition"]
    res = http.post(f"{API}/templates", json={"id": tpl, "name": prop["proposed_data"].get("name") or schema.get("document_type", "Roster"),
                                               "schema_definition": schema})
    res.raise_for_status()
    print(f"\nTemplate accepted, active version {res.json()['active_version_id']}")

    # 3. Process documents
    doc_ids = []
    for p in documents:
        doc = http.post(f"{API}/documents/upload", headers=h, files={"file": (p.name, p.read_bytes(), "image/jpeg")}).json()
        http.post(f"{API}/documents/{doc['id']}/process", headers=h).raise_for_status()
        doc_ids.append(doc["id"])
    print(f"Submitted {len(doc_ids)} document(s): {doc_ids}")

    for doc_id in doc_ids:
        def done():
            d = http.get(f"{API}/documents/{doc_id}", headers=h).json()
            return d if d["status"] not in ("QUEUED", "PROCESSING") else None
        d = wait_for(done, args.timeout, label=f"document {doc_id}")
        show(f"Document {doc_id}", {"status": d["status"], "error": d["error_message"]})
        ext = http.get(f"{API}/extractions/{doc_id}", headers=h)
        if ext.status_code == 200:
            e = ext.json()
            show("Record", {"data": e["extracted_data"], "confidence": e["overall_confidence"],
                            "validation": e["validation"], "notes": e["agent_notes"]})
        # The run's trace is reported right after the outcome; give it a moment.
        trace = wait_for(lambda: (r := http.get(f"{API}/documents/{doc_id}/trace", headers=h)).status_code == 200 and r,
                         60, every=2, label="trace")
        if trace:
            t = trace.json()
            calls = [c["tool_name"] for s in t["steps"] for c in s.get("tool_calls", [])]
            show("Agent run", {"status": t["status"], "steps": len(t["steps"]), "tools": calls,
                               "duration_s": round((t["total_duration_ms"] or 0) / 1000, 1), "summary": t["final_thought"]})

    show("Reviews", http.get(f"{API}/reviews", headers=h).json())
    show("Open questions", http.get(f"{API}/questions", headers=h).json())
    show("Pending proposals", http.get(f"{API}/knowledge/proposals", headers=h).json())
    show("Entities", http.get(f"{API}/knowledge/entities", headers=h).json())


if __name__ == "__main__":
    main()
