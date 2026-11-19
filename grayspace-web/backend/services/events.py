"""In-process events for GET /api/v1/events.

Events are JSON objects with a `type`:

- feature.update: a map feature's changed properties, keyed by the feature's id in /map, e.g.
  {"type": "feature.update", "id": "lum-chi:are-chi", "kind": "link", "properties": {"state": "down", "color": "#e74c3c"}}
- guest.update: a base router's or core service's Proxmox status, e.g.
  {"type": "guest.update", "hostname": "dns-google", "kind": "core-service", "status": "stopped", "previous": "running"}
- job.update: a job's state, stage and attempt
- instance.update: an instance's state
- action.applied: a runtime action on an instance

openapi/grayspace-events-v1.json is the schema (contract.py). Publishing never blocks: a
subscriber that falls behind loses its oldest events.
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

QUEUE_SIZE = 1000

_subscribers: Set[asyncio.Queue] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None
_lock = threading.Lock()


def subscribe() -> asyncio.Queue:
    global _loop
    _loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    with _lock:
        _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    with _lock:
        _subscribers.discard(queue)


def _deliver(event: Dict[str, Any]) -> None:
    with _lock:
        queues = list(_subscribers)
    for queue in queues:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(event)


def publish(event: Dict[str, Any]) -> None:
    event = {**event, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not _subscribers or _loop is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _deliver(event)
    elif not _loop.is_closed():
        _loop.call_soon_threadsafe(_deliver, event)


def publish_job(job) -> None:
    publish({"type": "job.update", "id": job.id, "kind": job.kind, "target": job.target, "state": job.state,
             "stage": job.stage, "attempt": job.attempt, "error": job.error})


def publish_instance(record) -> None:
    publish({"type": "instance.update", "id": record.id, "state": record.state, "error": record.error})


def feature_update(feature_id: str, kind: str, properties: Dict[str, Any]) -> None:
    publish({"type": "feature.update", "id": feature_id, "kind": kind, "properties": properties})


def guest_update(hostname: str, kind: str, status: str, previous: str) -> None:
    publish({"type": "guest.update", "hostname": hostname, "kind": kind, "status": status, "previous": previous})
