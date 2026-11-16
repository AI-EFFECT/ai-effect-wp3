"""Read-only checks used by the one-shot standby and offline operator checks.

Only connection/timeouts and startup HTTP failures are transient. No workflow,
authorization, queue claim, reservation or algorithm endpoint is created here.
"""
import json
import time

import httpx

PROBE_SECONDS = 3
STARTUP_SECONDS = 30
POLL_SECONDS = 1


class TransientProbeError(RuntimeError):
    pass


class PermanentProbeError(ValueError):
    pass


def dependency_probe(redis, token, upstream, observer, adapter, *, bound=None, check_queues=True,
                     transport=None, monotonic=time.monotonic):
    deadline = monotonic() + PROBE_SECONDS

    def remaining():
        left = deadline - monotonic()
        if left <= 0:
            raise TransientProbeError("Read-only dependency probe exceeded its three-second budget")
        return min(1, left)

    def get(client, url, name, *, headers=None):
        response = client.get(url, headers=headers, timeout=remaining())
        if response.status_code in {408, 500, 502, 503, 504}:
            raise TransientProbeError(name + " is starting or temporarily unavailable")
        if response.status_code in {401, 403}:
            raise PermanentProbeError(name + " rejected authentication; verify the retained control credential")
        if response.status_code != 200:
            raise PermanentProbeError(name + " returned an unexpected status; inspect its endpoint/configuration")
        return response

    try:
        remaining()
        if not redis.ping():
            raise TransientProbeError("Redis is temporarily unavailable")
        # Read only; reject unrelated accepted work even when its queue is empty.
        for key in redis.scan_iter("workflow:*") if check_queues else []:
            remaining()
            if key.count(":") == 1 and key != "workflow:" + str(bound):
                if json.loads(redis.get(key))["status"] not in {"completed", "failed"}:
                    raise PermanentProbeError("Unresolved unrelated workflow; reconcile it before activation")
        for key in redis.scan_iter("queue:*") if check_queues else []:
            remaining()
            if key != "queue:" + str(bound) and redis.llen(key):
                raise PermanentProbeError("Unresolved unrelated queue; reconcile it before activation")
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False) as client:
            schema = get(client, upstream + "/openapi.json", "ML API").json()
            if not all(path in schema.get("paths", {}) for path in ("/train", "/generate", "/models")):
                raise PermanentProbeError("ML API schema differs from the retained Portuguese API")
            get(client, observer + "/health", "Model observer")
            response = get(client, adapter + "/control/capabilities", "Adapter",
                           headers={"Authorization": "Bearer " + token})
            from .runtime import capabilities
            if response.json() != capabilities():
                raise PermanentProbeError("Adapter capability/contract mismatch; verify the exact candidate image")
        remaining()  # A late response must not produce a healthy lease.
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        raise TransientProbeError("Read-only dependency connection/DNS/timeout failure") from exc
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise PermanentProbeError("Dependency inspection response is invalid; verify its schema/configuration") from exc
    except (TransientProbeError, PermanentProbeError):
        raise
    except Exception as exc:
        # Import lazily: the pure HTTP/mock checks do not require Redis installed.
        from redis.exceptions import ConnectionError, TimeoutError
        if isinstance(exc, (ConnectionError, TimeoutError)):
            raise TransientProbeError("Read-only Redis connection/timeout failure") from exc
        raise
