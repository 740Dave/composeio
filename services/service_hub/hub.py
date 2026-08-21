"""ComposeIO Service Hub v2 — workload + ComposeApp supervisor.

DP-CIO-24: Compute-intensive work runs on a service hub, not edge devices.

Two tiers:
  1. Workloads — Python scripts with venv isolation, hub-managed restarts
  2. ComposeApps — Docker containers with dual-bus architecture, Docker-managed restarts

Workload format:
    workload: camera_frame_server
    runtime: python
    entry: server.py
    requirements: requirements.txt
    restart: always
    namespaces:
        publishes: [camera_front_frame_ready]
        subscribes: [camera_select]

ComposeApp format:
    .compose.yaml with app, docker, and namespaces sections.
    See SPEC:CIO-SERVICE-HUB-V2 for full schema.
"""

from __future__ import annotations
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("service_hub")

# ---------------------------------------------------------------------------
# Directories
# ---------------------------------------------------------------------------
HUB_DIR = Path(os.environ.get("COMPOSEIO_HUB_DIR", "/var/lib/composeio/hub"))
WORKLOADS_DIR = HUB_DIR / "workloads"
VENVS_DIR = HUB_DIR / "venvs"
APPS_DIR = HUB_DIR / "apps"

# Bus storage URL
BUS_STORAGE_URL = os.environ.get("COMPOSEIO_STORAGE_URL", "http://localhost:8420")

# Hub API port / bind host
HUB_HOST = os.environ.get("COMPOSEIO_HUB_HOST", "0.0.0.0")
HUB_PORT = int(os.environ.get("COMPOSEIO_HUB_PORT", "8890"))

# Hub version
HUB_VERSION = "2.0.0"


# ---------------------------------------------------------------------------
# Minimal YAML parser — stdlib only, handles the .compose.yaml subset
# ---------------------------------------------------------------------------
def _parse_yaml(text: str) -> dict:
    """Parse a minimal YAML subset sufficient for .compose.yaml files.

    Supports:
      - Scalar values (strings, integers, booleans)
      - Nested mappings (indentation-based)
      - Sequences (``- item`` syntax, including quoted items with colons)
      - Quoted strings (single and double)
      - Comments (``#``)

    Does NOT support: anchors, aliases, multi-line scalars, flow syntax.
    Uses a recursive descent approach for correct nesting.
    """
    lines = text.split("\n")
    return _parse_block(lines, 0, 0)[0]


def _parse_block(lines: list[str], start: int, min_indent: int) -> tuple[dict, int]:
    """Parse a YAML block starting at `start` with minimum indent `min_indent`.
    Returns (parsed_dict, next_line_index).
    """
    result: dict = {}
    i = start
    while i < len(lines):
        raw_line = lines[i]
        line = _strip_comment(raw_line)
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        indent = len(line) - len(line.lstrip())
        if indent < min_indent:
            break

        # Sequence items at this level are not expected at dict level
        if stripped.startswith("- "):
            break

        m = re.match(r"^(\S.*?):\s*(.*)?$", stripped)
        if not m:
            i += 1
            continue

        key = m.group(1).strip()
        val_part = (m.group(2) or "").strip()

        if val_part:
            result[key] = _yaml_scalar(val_part)
            i += 1
        else:
            # Look ahead to determine if child is a list or dict
            i += 1
            child_indent = None
            peek = i
            while peek < len(lines):
                peek_line = _strip_comment(lines[peek]).strip()
                if peek_line:
                    child_indent = len(_strip_comment(lines[peek])) - len(_strip_comment(lines[peek]).lstrip())
                    break
                peek += 1

            if child_indent is None or child_indent <= indent:
                result[key] = None
                continue

            # Check if child is a list
            peek_stripped = _strip_comment(lines[peek]).strip()
            if peek_stripped.startswith("- "):
                lst, i = _parse_list(lines, i, child_indent)
                result[key] = lst
            else:
                d, i = _parse_block(lines, i, child_indent)
                result[key] = d

    return result, i


def _parse_list(lines: list[str], start: int, min_indent: int) -> tuple[list, int]:
    """Parse a YAML list block. Returns (list, next_line_index)."""
    result: list = []
    i = start
    while i < len(lines):
        raw_line = lines[i]
        line = _strip_comment(raw_line)
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        indent = len(line) - len(line.lstrip())
        if indent < min_indent:
            break

        if stripped.startswith("- "):
            item_text = stripped[2:].strip()
            # Check if this is a mapping item (key: value after "- ")
            m = re.match(r"^(\S.*?):\s+(.*\S.*)$", item_text)
            if m:
                # It's a dict item in the list — parse as inline mapping
                item_dict = {m.group(1).strip(): _yaml_scalar(m.group(2).strip())}
                # Check for continuation lines at deeper indent
                child_indent = indent + 2
                j = i + 1
                while j < len(lines):
                    cl = _strip_comment(lines[j])
                    cs = cl.strip()
                    if not cs:
                        j += 1
                        continue
                    ci = len(cl) - len(cl.lstrip())
                    if ci < child_indent:
                        break
                    km = re.match(r"^(\S.*?):\s*(.*)?$", cs)
                    if km:
                        k = km.group(1).strip()
                        v = (km.group(2) or "").strip()
                        item_dict[k] = _yaml_scalar(v) if v else None
                    j += 1
                result.append(item_dict)
                i = j
            elif re.match(r"^(\S.*?):\s*$", item_text):
                # Dict item with nested value
                key_match = re.match(r"^(\S.*?):\s*$", item_text)
                item_dict = {}
                if key_match:
                    nested_key = key_match.group(1).strip()
                    child_indent = indent + 2
                    j = i + 1
                    sub, j = _parse_block(lines, j, child_indent)
                    item_dict[nested_key] = sub
                result.append(item_dict)
                i = j
            else:
                result.append(_yaml_scalar(item_text))
                i += 1
        else:
            break

    return result, i


def _strip_comment(line: str) -> str:
    """Remove trailing comments, respecting quoted strings."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _yaml_scalar(val: str):
    """Convert a YAML scalar string to a Python value."""
    if not val:
        return None
    # Strip quotes
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    # Booleans
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    if val.lower() == "null":
        return None
    # Integers
    try:
        return int(val)
    except ValueError:
        pass
    # Floats
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ---------------------------------------------------------------------------
# WorkloadProcess — UNCHANGED from v1
# ---------------------------------------------------------------------------
class WorkloadProcess:
    """Manages a single workload subprocess."""

    def __init__(self, workload_id: str, config: dict, workload_dir: Path):
        self.workload_id = workload_id
        self.config = config
        self.workload_dir = workload_dir
        self.process: subprocess.Popen | None = None
        self.restart_count = 0
        self.max_restarts = 10
        self.restart_delay = 5  # seconds
        self._running = False
        self._log_thread: Thread | None = None

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self):
        """Start the workload subprocess."""
        self._running = True
        venv_dir = VENVS_DIR / self.workload_id
        entry = self.config.get("entry", "main.py")
        entry_path = self.workload_dir / entry

        if not entry_path.exists():
            logger.error(f"[{self.workload_id}] Entry point not found: {entry_path}")
            return

        # Create/update venv if requirements exist
        reqs_file = self.workload_dir / self.config.get("requirements", "requirements.txt")
        if reqs_file.exists():
            self._setup_venv(venv_dir, reqs_file)

        # Determine python executable
        python = str(venv_dir / "bin" / "python3") if venv_dir.exists() else sys.executable

        # Start process
        env = os.environ.copy()
        env["COMPOSEIO_STORAGE_URL"] = BUS_STORAGE_URL
        env["COMPOSEIO_WORKLOAD_ID"] = self.workload_id

        logger.info(f"[{self.workload_id}] Starting: {python} {entry_path}")
        self.process = subprocess.Popen(
            [python, str(entry_path)],
            cwd=str(self.workload_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Start log forwarding thread
        self._log_thread = Thread(target=self._forward_logs, daemon=True)
        self._log_thread.start()

    def stop(self):
        """Stop the workload subprocess."""
        self._running = False
        if self.process and self.process.poll() is None:
            logger.info(f"[{self.workload_id}] Stopping (SIGTERM)")
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(f"[{self.workload_id}] Force killing (SIGKILL)")
                self.process.kill()

    def supervise(self):
        """Check if process crashed and restart if policy allows."""
        if not self._running:
            return

        if self.process and self.process.poll() is not None:
            exit_code = self.process.returncode
            restart_policy = self.config.get("restart", "always")

            if restart_policy == "always" and self.restart_count < self.max_restarts:
                self.restart_count += 1
                logger.warning(
                    f"[{self.workload_id}] Exited with code {exit_code}, "
                    f"restarting ({self.restart_count}/{self.max_restarts}) "
                    f"in {self.restart_delay}s"
                )
                time.sleep(self.restart_delay)
                self.start()
            elif restart_policy == "never":
                logger.info(f"[{self.workload_id}] Exited with code {exit_code}, not restarting")
                self._running = False
            else:
                logger.error(
                    f"[{self.workload_id}] Max restarts ({self.max_restarts}) reached, giving up"
                )
                self._running = False

    def _setup_venv(self, venv_dir: Path, reqs_file: Path):
        """Create or update a Python venv for the workload."""
        if not venv_dir.exists():
            logger.info(f"[{self.workload_id}] Creating venv at {venv_dir}")
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

        pip = str(venv_dir / "bin" / "pip")
        logger.info(f"[{self.workload_id}] Installing requirements from {reqs_file}")
        subprocess.run(
            [pip, "install", "-q", "-r", str(reqs_file)],
            check=True,
            capture_output=True,
        )

    def _forward_logs(self):
        """Forward subprocess stdout to logger."""
        if self.process and self.process.stdout:
            for line in iter(self.process.stdout.readline, b""):
                text = line.decode(errors="replace").rstrip()
                if text:
                    logger.info(f"[{self.workload_id}] {text}")

    def status(self) -> dict:
        return {
            "id": self.workload_id,
            "running": self.is_running,
            "pid": self.process.pid if self.process else None,
            "restart_count": self.restart_count,
            "config": self.config,
        }


# ---------------------------------------------------------------------------
# ComposeAppProcess — NEW: Docker container lifecycle for ComposeApps
# ---------------------------------------------------------------------------
class ComposeAppProcess:
    """Manages a single ComposeApp running as a Docker container.

    Reads configuration from a .compose.yaml file. The hub provisions the
    container (build/pull image, docker run with ports/volumes/env), monitors
    health via docker inspect, and reports status. Docker's own restart policy
    handles automatic restarts — the hub monitors but does not reimplement that.
    """

    def __init__(self, app_id: str, config: dict, app_dir: Path):
        self.app_id = app_id
        self.config = config
        self.app_dir = app_dir
        self.container_id: str | None = None
        self.container_name: str = config.get("docker", {}).get("container_name", app_id)
        self.restart_count = 0
        self._running = False
        self._last_running_state: bool | None = None

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def build_or_pull(self) -> bool:
        """Build or pull the Docker image. Returns True on success."""
        docker_cfg = self.config.get("docker", {})
        build_ctx = docker_cfg.get("build")
        image = docker_cfg.get("image")

        if build_ctx:
            return self._build_image(build_ctx)
        elif image:
            return self._pull_image(image)
        else:
            logger.error(f"[{self.app_id}] No 'build' or 'image' in docker config")
            return False

    def _build_image(self, build_context: str) -> bool:
        """Build a Docker image from a build context directory."""
        # Resolve build context relative to app directory
        if build_context == ".":
            ctx_path = str(self.app_dir)
        else:
            ctx_path = str(self.app_dir / build_context)

        tag = f"{self.app_id}:latest"
        logger.info(f"[{self.app_id}] Building Docker image {tag} from {ctx_path}")
        try:
            result = subprocess.run(
                ["docker", "build", "-t", tag, ctx_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"[{self.app_id}] Docker build failed:\n{result.stderr}")
                return False
            logger.info(f"[{self.app_id}] Docker build succeeded: {tag}")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"[{self.app_id}] Docker build timed out (300s)")
            return False
        except FileNotFoundError:
            logger.error(f"[{self.app_id}] Docker not found on PATH")
            return False

    def _pull_image(self, image: str) -> bool:
        """Pull a Docker image from a registry."""
        logger.info(f"[{self.app_id}] Pulling Docker image {image}")
        try:
            result = subprocess.run(
                ["docker", "pull", image],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"[{self.app_id}] Docker pull failed:\n{result.stderr}")
                return False
            logger.info(f"[{self.app_id}] Docker pull succeeded: {image}")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"[{self.app_id}] Docker pull timed out (300s)")
            return False
        except FileNotFoundError:
            logger.error(f"[{self.app_id}] Docker not found on PATH")
            return False

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the Docker container. Returns True on success."""
        docker_cfg = self.config.get("docker", {})

        # Determine image name — local build takes precedence over docker.image
        if docker_cfg.get("build"):
            image = f"{self.app_id}:latest"
        else:
            image = docker_cfg.get("image", f"{self.app_id}:latest")

        # Stop and remove any existing container with the same name
        self._remove_existing()

        # Build docker run command
        cmd = ["docker", "run", "-d", "--name", self.container_name]

        # Restart policy — default 'always' per spec SH-DEPLOY-CONFIG
        restart_policy = docker_cfg.get("restart", "always")
        if restart_policy and restart_policy != "no":
            cmd.extend(["--restart", str(restart_policy)])

        # Port bindings
        ports = docker_cfg.get("ports", [])
        for port in ports:
            cmd.extend(["-p", str(port)])

        # Volumes
        volumes = docker_cfg.get("volumes", [])
        for vol in volumes:
            cmd.extend(["-v", str(vol)])

        # Environment variables from config
        env_vars = docker_cfg.get("environment", {})
        if isinstance(env_vars, dict):
            for k, v in env_vars.items():
                cmd.extend(["-e", f"{k}={v}"])
        elif isinstance(env_vars, list):
            for item in env_vars:
                cmd.extend(["-e", str(item)])

        # Inject transport credentials (override any from config to ensure correctness)
        transport_env = self._build_transport_env()
        for k, v in transport_env.items():
            cmd.extend(["-e", f"{k}={v}"])

        cmd.append(image)

        logger.info(f"[{self.app_id}] Starting container: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.error(f"[{self.app_id}] Docker run failed:\n{result.stderr}")
                return False
            self.container_id = result.stdout.strip()[:12]
            self._running = True
            self._last_running_state = True
            logger.info(f"[{self.app_id}] Container started: {self.container_id}")
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"[{self.app_id}] Docker run timed out (60s)")
            return False
        except FileNotFoundError:
            logger.error(f"[{self.app_id}] Docker not found on PATH")
            return False

    def stop(self, timeout: int = 10) -> bool:
        """Stop the container gracefully (SIGTERM, then SIGKILL after timeout)."""
        logger.info(f"[{self.app_id}] Stopping container {self.container_name} (timeout={timeout}s)")
        self._running = False
        try:
            result = subprocess.run(
                ["docker", "stop", "-t", str(timeout), self.container_name],
                capture_output=True,
                text=True,
                timeout=timeout + 15,
            )
            if result.returncode != 0:
                logger.warning(f"[{self.app_id}] Docker stop returned non-zero:\n{result.stderr}")
                return False
            logger.info(f"[{self.app_id}] Container stopped")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"[{self.app_id}] Docker stop timed out, force killing")
            subprocess.run(
                ["docker", "kill", self.container_name],
                capture_output=True,
                text=True,
            )
            return True
        except FileNotFoundError:
            logger.error(f"[{self.app_id}] Docker not found on PATH")
            return False

    def restart(self) -> bool:
        """Restart the container."""
        logger.info(f"[{self.app_id}] Restarting container {self.container_name}")
        try:
            result = subprocess.run(
                ["docker", "restart", self.container_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.error(f"[{self.app_id}] Docker restart failed:\n{result.stderr}")
                return False
            self._running = True
            self._last_running_state = True
            logger.info(f"[{self.app_id}] Container restarted")
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"[{self.app_id}] Docker restart error: {e}")
            return False

    def deploy(self) -> bool:
        """Full deploy: build/pull image then start container."""
        if not self.build_or_pull():
            return False
        return self.start()

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    def check_health(self) -> dict:
        """Check container health via docker inspect. Returns status dict."""
        try:
            result = subprocess.run(
                [
                    "docker", "inspect",
                    "--format", "{{.State.Running}}|{{.State.ExitCode}}|{{.RestartCount}}|{{.State.Status}}",
                    self.container_name,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return {"running": False, "status": "not_found", "exit_code": -1}

            parts = result.stdout.strip().split("|")
            running = parts[0].lower() == "true" if parts else False
            exit_code = int(parts[1]) if len(parts) > 1 else -1
            docker_restart_count = int(parts[2]) if len(parts) > 2 else 0
            status_str = parts[3] if len(parts) > 3 else "unknown"

            # Track restart count from Docker
            if docker_restart_count > self.restart_count:
                self.restart_count = docker_restart_count

            # Log state transitions
            if self._last_running_state is not None and self._last_running_state and not running:
                logger.warning(
                    f"[{self.app_id}] Container stopped (exit_code={exit_code}, status={status_str})"
                )
            elif self._last_running_state is not None and not self._last_running_state and running:
                logger.info(f"[{self.app_id}] Container recovered (Docker restart policy)")

            self._last_running_state = running

            return {
                "running": running,
                "status": status_str,
                "exit_code": exit_code,
                "restart_count": self.restart_count,
            }
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            return {"running": False, "status": "inspect_error", "exit_code": -1}

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def get_logs(self, lines: int = 100) -> str:
        """Get the last N lines of container logs."""
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(lines), self.container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Docker sends some logs to stderr
            return result.stdout + result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return f"[error] Could not retrieve logs for {self.container_name}"

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """Full status for API responses."""
        docker_cfg = self.config.get("docker", {})
        health = self.check_health()

        # Collect exported namespaces
        exported = []
        for ns in self.config.get("namespaces", []):
            if isinstance(ns, dict) and ns.get("export"):
                exported.append(ns.get("name", ""))

        return {
            "id": self.app_id,
            "running": health.get("running", False),
            "container_id": self.container_id,
            "container_name": self.container_name,
            "image": docker_cfg.get("image", f"{self.app_id}:latest"),
            "ports": docker_cfg.get("ports", []),
            "health": health.get("status", "unknown"),
            "restart_count": health.get("restart_count", self.restart_count),
            "exported_namespaces": exported,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_transport_env(self) -> dict:
        """Build MQTT transport environment variables from app config."""
        env = {}
        app_cfg = self.config.get("app", {})
        env["COMPOSEIO_APP_ID"] = app_cfg.get("id", self.app_id)
        env["COMPOSEIO_STORAGE_URL"] = BUS_STORAGE_URL

        # Find MQTT transport in config
        transports = app_cfg.get("transports", [])
        for t in transports:
            if isinstance(t, dict) and t.get("type") == "mqtt":
                env["COMPOSEIO_MQTT_BROKER"] = str(t.get("broker", "mqtt-broker.local"))
                env["COMPOSEIO_MQTT_PORT"] = str(t.get("port", 1883))
                break
        else:
            # Defaults if no MQTT transport declared
            env.setdefault("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")
            env.setdefault("COMPOSEIO_MQTT_PORT", "1883")

        return env

    def _remove_existing(self):
        """Remove an existing container with the same name (if any)."""
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
        )


# ---------------------------------------------------------------------------
# Hub API — HTTP server for status and management
# ---------------------------------------------------------------------------
class HubAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Service Hub API."""

    hub: ServiceHub  # Set by the factory

    def log_message(self, format, *args):
        """Route HTTP server logs through our logger."""
        logger.debug(f"HTTP {format % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/api/v1/status":
            self._json_response(200, self.hub.status())

        elif path == "/api/v1/workloads":
            data = self.hub.workload_statuses()
            self._json_response(200, data)

        elif path == "/api/v1/apps":
            data = self.hub.app_statuses()
            self._json_response(200, data)

        elif path.startswith("/api/v1/apps/"):
            # Parse: /api/v1/apps/{id} or /api/v1/apps/{id}/{sub}
            parts = path.split("/")
            # parts: ['', 'api', 'v1', 'apps', app_id, ...]
            if len(parts) < 5 or not parts[4]:
                self._json_response(404, {"error": "Not found"})
                return

            app_id = parts[4]
            sub = parts[5] if len(parts) > 5 else None

            if sub is None:
                # GET /api/v1/apps/{id}
                app = self.hub.get_app(app_id)
                if app is not None:
                    with self.hub.get_app_lock(app_id):
                        detail = app.status()
                    detail["namespaces"] = app.config.get("namespaces", [])
                    self._json_response(200, detail)
                else:
                    self._json_response(404, {"error": f"App '{app_id}' not found"})

            elif sub == "logs":
                # GET /api/v1/apps/{id}/logs
                app = self.hub.get_app(app_id)
                if app is not None:
                    lines_param = params.get("lines", ["100"])
                    try:
                        n = int(lines_param[0])
                    except ValueError:
                        n = 100
                    with self.hub.get_app_lock(app_id):
                        logs = app.get_logs(n)
                    self._json_response(200, {"app_id": app_id, "lines": n, "logs": logs})
                else:
                    self._json_response(404, {"error": f"App '{app_id}' not found"})

            else:
                self._json_response(404, {"error": "Not found"})

        else:
            self._json_response(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not path.startswith("/api/v1/apps/"):
            self._json_response(404, {"error": "Not found"})
            return

        parts = path.split("/")
        if len(parts) < 6:
            self._json_response(400, {"error": "Invalid path"})
            return

        app_id = parts[4]
        action = parts[5]

        # Auto-discover-and-register is a read-modify-write on the shared apps
        # registry; ensure_app_loaded does it atomically under the registry lock.
        app = self.hub.ensure_app_loaded(app_id)

        if app is None:
            self._json_response(404, {"error": f"App '{app_id}' not found"})
            return

        # Serialize mutating lifecycle actions on THIS app (deploy/stop/restart
        # and the supervise loop's check_health) via the per-app lock, while
        # actions on other apps still run in parallel.
        lock = self.hub.get_app_lock(app_id)

        if action == "deploy":
            with lock:
                success = app.deploy()
                app_status = app.status()
            status_code = 200 if success else 500
            self._json_response(status_code, {
                "app_id": app_id,
                "action": "deploy",
                "success": success,
                "status": app_status,
            })

        elif action == "stop":
            with lock:
                success = app.stop()
            self._json_response(200, {
                "app_id": app_id,
                "action": "stop",
                "success": success,
            })

        elif action == "restart":
            with lock:
                success = app.restart()
                app_status = app.status()
            status_code = 200 if success else 500
            self._json_response(status_code, {
                "app_id": app_id,
                "action": "restart",
                "success": success,
                "status": app_status,
            })

        else:
            self._json_response(400, {"error": f"Unknown action: {action}"})

    def _json_response(self, status_code: int, data: dict | list):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _make_handler(hub: ServiceHub):
    """Create a handler class bound to a specific hub instance."""

    class BoundHandler(HubAPIHandler):
        pass

    BoundHandler.hub = hub
    return BoundHandler


class _ThreadingServer(ThreadingHTTPServer):
    # One thread per request so a single slow/wedged request can't block the
    # whole hub API (the 2026-07-10 SPOF: a wedged request filled the default
    # accept backlog and stranded clients).
    daemon_threads = True  # request threads never block shutdown
    # Larger listen backlog than the default 5 so a burst of clients / a hung
    # request can't fill the accept queue and strand other callers.
    request_queue_size = 128
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# ServiceHub — extended to manage both workloads and ComposeApps
# ---------------------------------------------------------------------------
class ServiceHub:
    """Workload + ComposeApp supervisor — manages all processes and containers."""

    def __init__(self):
        self.workloads: dict[str, WorkloadProcess] = {}
        self.apps: dict[str, ComposeAppProcess] = {}
        self._running = False
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: Thread | None = None
        # --- Concurrency (ThreadingHTTPServer runs one thread per request) ---
        # The registry lock guards the SHAPE of the workloads/apps dicts and the
        # per-app lock table (membership, inserts, snapshots) — held only
        # briefly, never across a subprocess/docker call. Per-app locks serialize
        # mutating lifecycle work (deploy/stop/restart/check_health) on a SINGLE
        # app so request handlers and the supervise/auto-deploy background
        # threads can't race on one app's fields, while different apps still run
        # in parallel. Registry and per-app locks are never held simultaneously,
        # so there is no lock-ordering hazard.
        self._registry_lock = threading.RLock()
        self._app_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Thread-safe registry access helpers
    # ------------------------------------------------------------------

    def get_app_lock(self, app_id: str) -> threading.Lock:
        """Return the stable per-app lock, creating it on first use."""
        with self._registry_lock:
            lock = self._app_locks.get(app_id)
            if lock is None:
                lock = threading.Lock()
                self._app_locks[app_id] = lock
            return lock

    def _snapshot_workloads(self) -> list[tuple[str, WorkloadProcess]]:
        with self._registry_lock:
            return list(self.workloads.items())

    def _snapshot_apps(self) -> list[tuple[str, ComposeAppProcess]]:
        with self._registry_lock:
            return list(self.apps.items())

    def get_app(self, app_id: str) -> ComposeAppProcess | None:
        with self._registry_lock:
            return self.apps.get(app_id)

    def workload_statuses(self) -> dict:
        # Snapshot under the lock, then build statuses off the lock (wp.status()
        # only reads process state; no subprocess calls).
        return {wid: wp.status() for wid, wp in self._snapshot_workloads()}

    def app_statuses(self) -> dict:
        # Snapshot the membership under the registry lock, then compute each
        # status under its per-app lock (status() shells out to docker inspect,
        # so it must NOT run while holding the registry lock).
        out = {}
        for aid, ap in self._snapshot_apps():
            with self.get_app_lock(aid):
                out[aid] = ap.status()
        return out

    def ensure_app_loaded(self, app_id: str) -> ComposeAppProcess | None:
        """Return the registered app, auto-discovering it from APPS_DIR if a
        directory exists. The final check-and-insert runs under the registry
        lock so concurrent POSTs can't double-register the same app."""
        app = self.get_app(app_id)
        if app is not None:
            return app
        app_dir = APPS_DIR / app_id
        if app_dir.is_dir():
            cfg = self._load_app_config(app_dir)  # disk I/O — done off the lock
            if cfg:
                with self._registry_lock:
                    app = self.apps.get(app_id)
                    if app is None:
                        app = ComposeAppProcess(app_id, cfg, app_dir)
                        self.apps[app_id] = app
                        logger.info(f"Auto-loaded app from directory: {app_id}")
                    return app
        return self.get_app(app_id)

    # ------------------------------------------------------------------
    # Workload loading — UNCHANGED from v1
    # ------------------------------------------------------------------

    def load_workloads(self):
        """Load workload definitions from bus storage."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(f"{BUS_STORAGE_URL}/api/v1/workloads")
            workload_list = json.loads(resp.read())
            for wl in workload_list:
                wl_id = wl.get("id", wl.get("workload", "unknown"))
                workload_dir = WORKLOADS_DIR / wl_id
                workload_dir.mkdir(parents=True, exist_ok=True)

                # Save workload config
                (workload_dir / "workload.json").write_text(json.dumps(wl, indent=2))

                self.workloads[wl_id] = WorkloadProcess(wl_id, wl, workload_dir)
                logger.info(f"Loaded workload: {wl_id}")
        except Exception as e:
            logger.warning(f"Could not load workloads from bus storage: {e}")
            # Fall back to local workload directories
            if WORKLOADS_DIR.exists():
                for d in WORKLOADS_DIR.iterdir():
                    if d.is_dir() and (d / "workload.json").exists():
                        wl = json.loads((d / "workload.json").read_text())
                        wl_id = wl.get("id", d.name)
                        self.workloads[wl_id] = WorkloadProcess(wl_id, wl, d)
                        logger.info(f"Loaded local workload: {wl_id}")

    # ------------------------------------------------------------------
    # ComposeApp loading — NEW
    # ------------------------------------------------------------------

    def load_apps(self):
        """Load ComposeApp definitions from bus storage or local apps directory."""
        import urllib.request

        # Try bus storage first
        try:
            resp = urllib.request.urlopen(f"{BUS_STORAGE_URL}/api/v1/apps")
            app_list = json.loads(resp.read())
            for app_data in app_list:
                app_id = app_data.get("id", app_data.get("app", {}).get("id", "unknown"))
                app_dir = APPS_DIR / app_id
                app_dir.mkdir(parents=True, exist_ok=True)

                # Save the config locally
                yaml_files = list(app_dir.glob("*.compose.yaml"))
                if not yaml_files:
                    # Write config as JSON fallback — app_data is the parsed config
                    config_path = app_dir / f"{app_id}.compose.json"
                    config_path.write_text(json.dumps(app_data, indent=2))

                self.apps[app_id] = ComposeAppProcess(app_id, app_data, app_dir)
                logger.info(f"Loaded app from bus storage: {app_id}")
        except Exception as e:
            logger.warning(f"Could not load apps from bus storage: {e}")

        # Also load from local apps directory (supplements bus storage results)
        if APPS_DIR.exists():
            for d in APPS_DIR.iterdir():
                if d.is_dir() and d.name not in self.apps:
                    cfg = self._load_app_config(d)
                    if cfg:
                        app_id = cfg.get("app", {}).get("id", d.name)
                        self.apps[app_id] = ComposeAppProcess(app_id, cfg, d)
                        logger.info(f"Loaded local app: {app_id}")

    def _load_app_config(self, app_dir: Path) -> dict | None:
        """Load a .compose.yaml (or .compose.json) from an app directory."""
        # Try .compose.yaml files
        yaml_files = list(app_dir.glob("*.compose.yaml"))
        if yaml_files:
            try:
                text = yaml_files[0].read_text()
                return _parse_yaml(text)
            except Exception as e:
                logger.error(f"Failed to parse {yaml_files[0]}: {e}")
                return None

        # Try .compose.json fallback
        json_files = list(app_dir.glob("*.compose.json"))
        if json_files:
            try:
                return json.loads(json_files[0].read_text())
            except Exception as e:
                logger.error(f"Failed to parse {json_files[0]}: {e}")
                return None

        # Try plain .yaml as last resort
        yaml_files = list(app_dir.glob("*.yaml"))
        if yaml_files:
            try:
                text = yaml_files[0].read_text()
                return _parse_yaml(text)
            except Exception as e:
                logger.error(f"Failed to parse {yaml_files[0]}: {e}")
                return None

        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start all workloads and begin monitoring apps."""
        self._running = True

        # Start workloads (existing behavior)
        logger.info(f"Starting {len(self.workloads)} workloads")
        for _wid, wp in self._snapshot_workloads():
            wp.start()

        # Start containers for all registered apps
        logger.info(f"Starting {len(self.apps)} ComposeApps")
        for aid, app in self._snapshot_apps():
            # Check if container is already running (e.g., survived hub restart).
            # The API is already live, so guard the per-app read-modify-write.
            with self.get_app_lock(aid):
                health = app.check_health()
                already_running = bool(health.get("running"))
                if already_running:
                    app._running = True
            if already_running:
                logger.info(f"[{app.app_id}] Container already running")
            else:
                # Auto-deploy: build/pull image and start the container
                logger.info(f"[{app.app_id}] Container not running — auto-deploying")
                deploy_thread = Thread(
                    target=self._auto_deploy_app, args=(app,), daemon=True
                )
                deploy_thread.start()

    def _auto_deploy_app(self, app: ComposeAppProcess):
        """Deploy an app in a background thread (used during hub startup)."""
        try:
            # Hold the per-app lock so this startup deploy can't race a concurrent
            # deploy/stop/restart request or the supervise loop on the same app.
            with self.get_app_lock(app.app_id):
                success = app.deploy()
            if success:
                logger.info(f"[{app.app_id}] Auto-deploy succeeded")
            else:
                logger.error(f"[{app.app_id}] Auto-deploy failed")
        except Exception as e:
            logger.error(f"[{app.app_id}] Auto-deploy error: {e}")

    def stop(self):
        """Stop all workloads and containers."""
        self._running = False

        # Stop HTTP API
        if self._http_server:
            self._http_server.shutdown()

        # Stop workloads
        for _wid, wp in self._snapshot_workloads():
            wp.stop()

        # Stop app containers
        for aid, app in self._snapshot_apps():
            with self.get_app_lock(aid):
                if app._running:
                    app.stop()

    def start_api(self):
        """Start the HTTP API server in a background thread."""
        handler_class = _make_handler(self)
        self._http_server = _ThreadingServer((HUB_HOST, HUB_PORT), handler_class)
        self._http_thread = Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        logger.info(f"Hub API listening on port {HUB_PORT}")

    def supervise_loop(self):
        """Main supervision loop — check workloads (2s) and apps (5s cycle)."""
        app_check_counter = 0
        while self._running:
            # Workload supervision — every 2 seconds (unchanged). Snapshot the
            # registry so a concurrent auto-discover insert can't break iteration.
            for _wid, wp in self._snapshot_workloads():
                wp.supervise()

            # App supervision — every ~5 seconds (every 3rd iteration of the 2s loop)
            # All registered apps are monitored regardless of _running flag.
            # A container that Docker restarted (or that died with restart=no)
            # must still be visible to the supervision loop.
            app_check_counter += 1
            if app_check_counter >= 3:
                app_check_counter = 0
                for aid, app in self._snapshot_apps():
                    # Per-app lock: this read-modify-write of app._running now
                    # runs concurrently with request handlers touching the same
                    # app (deploy/stop/restart/status).
                    with self.get_app_lock(aid):
                        health = app.check_health()
                        # Sync _running with actual container state so status()
                        # and stop() stay consistent with reality.
                        if health.get("running") and not app._running:
                            app._running = True
                            transitioned = True
                        elif not health.get("running") and app._running:
                            app._running = False
                            transitioned = False
                        else:
                            transitioned = None
                    if transitioned is True:
                        logger.info(f"[{app.app_id}] Container now running (Docker restart policy)")

            time.sleep(2)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "service": "composeio-service-hub",
            "version": HUB_VERSION,
            "workloads": self.workload_statuses(),
            "apps": self.app_statuses(),
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    WORKLOADS_DIR.mkdir(parents=True, exist_ok=True)
    VENVS_DIR.mkdir(parents=True, exist_ok=True)
    APPS_DIR.mkdir(parents=True, exist_ok=True)

    hub = ServiceHub()
    hub.load_workloads()
    hub.load_apps()

    # Start HTTP API
    hub.start_api()

    # Start workloads and begin monitoring apps
    hub.start()

    # Handle shutdown signals
    def shutdown(sig, frame):
        logger.info(f"Received signal {sig}, shutting down")
        hub.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Run supervision loop
    hub.supervise_loop()


if __name__ == "__main__":
    main()
