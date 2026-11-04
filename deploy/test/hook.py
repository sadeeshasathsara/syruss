"""
The test server's one endpoint: POST /deploy. CI calls it after the images are published and it pulls them.

Standard library only (about 15 MB of memory). It does exactly one thing, whatever the request says:
`docker compose pull` and `up -d` for the named services (from a fixed list), in the deploy folder.

A request is accepted only with a valid signature: hex HMAC-SHA256 of "<timestamp>.<body>" with the shared
secret, in X-Syruss-Signature, and a timestamp within five minutes (X-Syruss-Timestamp), so a captured
request cannot be replayed later. Body: {"services": ["server", "www"], "compose": "<base64 compose file>"}.
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SECRET = os.environ["HOOK_SECRET"].encode()
FOLDER = Path(os.environ.get("DEPLOY_DIR", str(Path.home() / "syruss-deploy")))
PORT = int(os.environ.get("HOOK_PORT", "9100"))
HOST = os.environ.get("HOOK_HOST", "127.0.0.1")  # the reverse proxy (Caddy) is the only way in
SERVICES = {"postgres", "keycloak", "server", "agent", "www", "site"}
MAX_BODY = 64 * 1024
MAX_SKEW_SECONDS = 300

lock = threading.Lock()  # one deployment at a time


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)


def compose(*args: str) -> int:
    done = subprocess.run(
        ["docker", "compose", "--env-file", "env/stack.env", *args], cwd=FOLDER, capture_output=True, text=True, timeout=1800,
    )
    for out in (done.stdout, done.stderr):
        if out.strip():
            log(out.strip()[-1500:])
    return done.returncode


def deploy(services: list, compose_file: bytes | None) -> None:
    with lock:
        if compose_file:
            target = FOLDER / "docker-compose.yml"
            if not target.exists() or target.read_bytes() != compose_file:
                target.write_bytes(compose_file)
                log("compose file updated")
        names = services or sorted(SERVICES)
        log(f"deploying: {', '.join(names)}")
        if compose("pull", *names) != 0:
            log("pull failed; nothing restarted")
            return
        # The whole stack, so infrastructure containers (Keycloak, Kafka, MinIO) start too. Compose recreates
        # only containers whose image or configuration changed.
        code = compose("up", "-d", "--remove-orphans")
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, timeout=300)
        log("done" if code == 0 else f"up failed ({code})")


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, text: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(text)))
        self.end_headers()
        self.wfile.write(text.encode())

    def do_POST(self) -> None:
        if self.path != "/deploy":
            return self.reply(404)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self.reply(413)
        body = self.rfile.read(length)
        stamp = self.headers.get("X-Syruss-Timestamp", "")
        try:
            fresh = abs(time.time() - int(stamp)) <= MAX_SKEW_SECONDS
        except ValueError:
            fresh = False
        expected = hmac.new(SECRET, stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        if not fresh or not hmac.compare_digest(expected, self.headers.get("X-Syruss-Signature", "")):
            log(f"refused request from {self.client_address[0]}")
            return self.reply(401)
        try:
            payload = json.loads(body)
            services = [s for s in payload.get("services", []) if s in SERVICES]
            compose_file = base64.b64decode(payload["compose"]) if payload.get("compose") else None
        except (ValueError, TypeError, KeyError):
            return self.reply(400)
        threading.Thread(target=deploy, args=(services, compose_file), daemon=True).start()
        self.reply(202, "deploying")

    def log_message(self, *_args) -> None:  # request lines are logged by log() where they matter
        pass


if __name__ == "__main__":
    log(f"listening on {HOST}:{PORT}, deploying from {FOLDER}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
