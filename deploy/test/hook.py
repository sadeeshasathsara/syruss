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
SERVICES = {"postgres", "keycloak", "keycloak-db", "kafka", "minio", "server", "agent", "www", "site", "admin"}
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


# Start order: what others rely on comes first. Each service is pulled and started on its own, and the next one
# waits until it is ready and the machine has calmed down, because starting everything at once starves a small server.
ORDER = ["postgres", "keycloak-db", "kafka", "minio", "keycloak", "server", "agent", "www", "site", "admin"]
# Services without a healthcheck: how long to let them settle after they start.
SETTLE_SECONDS = {"minio": 10, "agent": 20, "www": 15, "site": 15, "admin": 15}
READY_TIMEOUT = 900   # per service, first start of Keycloak included
CALM_LOAD = 3.0       # one-minute load average to wait for before starting the next service
CALM_TIMEOUT = 240


def compose_out(*args: str) -> str:
    done = subprocess.run(
        ["docker", "compose", "--env-file", "env/stack.env", *args], cwd=FOLDER, capture_output=True, text=True, timeout=120,
    )
    return done.stdout.strip()


def container_state(name: str) -> tuple:
    """(running, health) of a service's container; health is '' when the image has no healthcheck."""
    cid = compose_out("ps", "-q", name)
    if not cid:
        return False, ""
    done = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", cid],
        capture_output=True, text=True, timeout=60,
    )
    parts = done.stdout.split()
    return (bool(parts) and parts[0] == "true"), (parts[1] if len(parts) > 1 else "")


def wait_ready(name: str) -> bool:
    """Until the service is up: healthy when it has a healthcheck, otherwise running and settled."""
    deadline = time.time() + READY_TIMEOUT
    while time.time() < deadline:
        running, health = container_state(name)
        if health == "healthy":
            return True
        if running and not health:
            time.sleep(SETTLE_SECONDS.get(name, 10))
            return container_state(name)[0]
        time.sleep(5)
    return False


def wait_calm() -> None:
    """Hold the next start until the machine is no longer busy with the last one."""
    deadline = time.time() + CALM_TIMEOUT
    while time.time() < deadline and os.getloadavg()[0] > CALM_LOAD:
        time.sleep(5)


def deploy(services: list, compose_file: bytes | None) -> None:
    with lock:
        if compose_file:
            target = FOLDER / "docker-compose.yml"
            if not target.exists() or target.read_bytes() != compose_file:
                target.write_bytes(compose_file)
                log("compose file updated")
        wanted = set(services) if services else set(SERVICES)
        queue = [name for name in ORDER if name in wanted or not container_state(name)[0]]
        log(f"queue: {', '.join(queue)}")
        for name in queue:
            wait_calm()
            if name in wanted:
                log(f"pulling {name}")
                if compose("pull", name) != 0:
                    log(f"pull of {name} failed; leaving it as it is")
                    continue
            log(f"starting {name}")
            if compose("up", "-d", "--no-deps", name) != 0:
                log(f"could not start {name}; continuing with the next")
                continue
            log(f"{name} is up" if wait_ready(name) else f"{name} did not become ready in time; continuing")
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, timeout=300)
        log("done")


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
