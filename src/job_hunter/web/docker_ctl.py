"""Docker container control for the LLM sidecar.

Provides a thin wrapper around the Docker SDK to start / stop / restart
the LLM sidecar container from the web UI.  All methods are synchronous
(call via ``asyncio.to_thread`` from async endpoints).

Gracefully degrades when:
- The ``docker`` package is not installed.
- The Docker socket is not reachable (local dev, missing mount).
- The target container does not exist.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("job_hunter.web.docker_ctl")

# Default container name — overridden by env var in Docker Compose
LLM_CONTAINER_NAME = os.environ.get("LLM_CONTAINER_NAME", "ai-job-hunter-llm")


def _get_client():
    """Return a Docker client, or ``None`` if unavailable."""
    try:
        import docker
        return docker.from_env(timeout=5)
    except Exception as exc:
        logger.debug("Docker SDK unavailable: %s", exc)
        return None


def available() -> bool:
    """Return ``True`` if the Docker socket is reachable."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.ping()
        return True
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def container_status(name: str = LLM_CONTAINER_NAME) -> dict[str, Any]:
    """Return status information for the LLM sidecar container.

    Keys: ``available``, ``state``, ``health``, ``uptime``, ``image``, ``error``.
    """
    client = _get_client()
    if client is None:
        return {
            "available": False,
            "state": "unknown",
            "health": "unknown",
            "uptime": None,
            "image": None,
            "error": "Docker is not available (SDK missing or socket not mounted)",
        }
    try:
        container = client.containers.get(name)
        state = container.status  # running, exited, paused, created, …
        health = "unknown"
        health_detail = container.attrs.get("State", {}).get("Health", {})
        if health_detail:
            health = health_detail.get("Status", "unknown")  # healthy, unhealthy, starting

        # Compute uptime
        uptime = None
        started_at_raw = container.attrs.get("State", {}).get("StartedAt", "")
        if started_at_raw and state == "running":
            try:
                started = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - started
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                if hours > 24:
                    days = hours // 24
                    uptime = f"{days}d {hours % 24}h"
                else:
                    uptime = f"{hours}h {minutes}m"
            except Exception:
                pass

        image = None
        try:
            image = container.image.tags[0] if container.image.tags else str(container.image.id)[:20]
        except Exception:
            pass

        return {
            "available": True,
            "state": state,
            "health": health,
            "uptime": uptime,
            "image": image,
            "error": None,
        }
    except Exception as exc:
        err_type = type(exc).__name__
        return {
            "available": True,  # Docker is reachable, but container issue
            "state": "not_found",
            "health": "unknown",
            "uptime": None,
            "image": None,
            "error": f"{err_type}: {exc}",
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


def start_container(name: str = LLM_CONTAINER_NAME) -> dict[str, Any]:
    """Start the LLM sidecar container."""
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "Docker not available"}
    try:
        container = client.containers.get(name)
        if container.status == "running":
            return {"ok": True, "message": "Already running"}
        container.start()
        return {"ok": True, "message": "Container started"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def stop_container(name: str = LLM_CONTAINER_NAME) -> dict[str, Any]:
    """Stop the LLM sidecar container."""
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "Docker not available"}
    try:
        container = client.containers.get(name)
        if container.status != "running":
            return {"ok": True, "message": "Already stopped"}
        container.stop(timeout=15)
        return {"ok": True, "message": "Container stopped"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def restart_container(name: str = LLM_CONTAINER_NAME) -> dict[str, Any]:
    """Restart the LLM sidecar container."""
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "Docker not available"}
    try:
        container = client.containers.get(name)
        container.restart(timeout=15)
        return {"ok": True, "message": "Container restarted"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            client.close()
        except Exception:
            pass


def container_logs(name: str = LLM_CONTAINER_NAME, tail: int = 80) -> dict[str, Any]:
    """Fetch recent log lines from the LLM sidecar container.

    Returns ``{"available": True, "logs": "…"}`` on success.
    """
    client = _get_client()
    if client is None:
        return {"available": False, "logs": "", "error": "Docker not available"}
    try:
        container = client.containers.get(name)
        raw = container.logs(tail=tail, timestamps=False)
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return {"available": True, "logs": text, "error": None}
    except Exception as exc:
        return {
            "available": True,
            "logs": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        try:
            client.close()
        except Exception:
            pass






