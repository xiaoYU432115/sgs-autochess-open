"""Opt-in, in-memory HTTP body metrics for the local bridge."""
from collections import Counter, deque
from math import ceil
from statistics import median
import threading
import time
from urllib.parse import urlsplit


_LOCK = threading.Lock()
_EVENTS = deque(maxlen=50000)
_STARTED_AT = None


def start():
    """Start a fresh sample window. No request or payload contents are retained."""
    global _STARTED_AT
    with _LOCK:
        _EVENTS.clear()
        _STARTED_AT = time.monotonic()


def record(method, path, request_bytes, response_bytes, status, response_ms=0.0):
    """Record HTTP body sizes and route metadata while sampling is active."""
    route = urlsplit(str(path or "")).path or "(unknown)"
    if route == "/transport_stats":
        return
    try:
        item = (
            time.monotonic(),
            str(method or "GET").upper(),
            route,
            max(0, int(request_bytes or 0)),
            max(0, int(response_bytes or 0)),
            int(status or 0),
            max(0.0, float(response_ms or 0.0)),
        )
    except (TypeError, ValueError):
        return
    with _LOCK:
        if _STARTED_AT is not None:
            _EVENTS.append(item)


def snapshot():
    now = time.monotonic()
    with _LOCK:
        started = _STARTED_AT
        events = list(_EVENTS)
        truncated = len(_EVENTS) == _EVENTS.maxlen
    if started is None:
        return {
            "enabled": False,
            "sampledSeconds": 0.0,
            "requests": 0,
            "requestsPerSecond": 0.0,
            "requestBodyBytes": 0,
            "responseBodyBytes": 0,
            "routes": [],
            "truncated": False,
        }

    elapsed = max(0.0, now - started)
    routes = {}
    statuses = Counter()
    request_bytes = response_bytes = 0
    response_times = []
    for _, method, route, req_size, resp_size, status, response_ms in events:
        key = (method, route)
        entry = routes.setdefault(key, {
            "method": method,
            "route": route,
            "requests": 0,
            "requestBodyBytes": 0,
            "responseBodyBytes": 0,
            "_responseTimes": [],
        })
        entry["requests"] += 1
        entry["requestBodyBytes"] += req_size
        entry["responseBodyBytes"] += resp_size
        entry["_responseTimes"].append(response_ms)
        response_times.append(response_ms)
        request_bytes += req_size
        response_bytes += resp_size
        statuses[status] += 1

    def percentile(values, fraction):
        ordered = sorted(values)
        if not ordered:
            return 0.0
        index = min(len(ordered) - 1, max(0, ceil(len(ordered) * fraction) - 1))
        return round(ordered[index], 2)

    route_rows = []
    for entry in routes.values():
        timings = entry.pop("_responseTimes")
        entry["handlerP95Ms"] = percentile(timings, 0.95)
        route_rows.append(entry)
    return {
        "enabled": True,
        "sampledSeconds": round(elapsed, 3),
        "requests": len(events),
        "requestsPerSecond": round(len(events) / elapsed, 3) if elapsed else 0.0,
        "requestBodyBytes": request_bytes,
        "responseBodyBytes": response_bytes,
        "handlerP50Ms": round(median(response_times), 2) if response_times else 0.0,
        "handlerP95Ms": percentile(response_times, 0.95),
        "routes": sorted(route_rows, key=lambda row: (-row["requests"], row["method"], row["route"])),
        "statusCodes": {str(code): count for code, count in sorted(statuses.items())},
        "truncated": truncated,
    }
