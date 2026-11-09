import json
import math
from collections import defaultdict

BAR_CLIENTS = 5000


def percentile(sorted_values, q):
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, math.ceil(q / 100 * len(sorted_values)) - 1))
    return sorted_values[k]


class Metrics:
    def __init__(self):
        self.samples = defaultdict(list)
        self.first = {}
        self.last = {}
        self.status = defaultdict(lambda: defaultdict(int))
        self.simulated = defaultdict(int)
        self.lease_flagged = set()
        self.hold_flagged = set()
        self.started_clients = 0
        self.finished_clients = 0
        self.client_failures = 0

    def record(self, endpoint, status, latency, now):
        """status is an int, or 'timeout' / 'error' for transport failures."""
        self.samples[endpoint].append(latency)
        self.status[endpoint][str(status)] += 1
        self.first.setdefault(endpoint, now)
        self.last[endpoint] = now

    def record_simulated(self, endpoint):
        self.simulated[endpoint] += 1

    def lease_expired(self, client):
        self.lease_flagged.add(client)

    def hold(self, client):
        self.hold_flagged.add(client)

    def summary(self, clients, ok_statuses=None):
        ok_statuses = ok_statuses or {}
        endpoints = {}
        total = errors = n429 = 0
        for name, values in sorted(self.samples.items()):
            ordered = sorted(values)
            span = max(self.last[name] - self.first[name], 1e-9)
            counts = dict(self.status[name])
            ok = set(ok_statuses.get(name, ("200", "201")))
            bad = sum(v for k, v in counts.items() if k not in ok)
            endpoints[name] = {
                "requests": len(values),
                "rps": round(len(values) / span, 2) if len(values) > 1 else None,
                "p50_ms": _ms(percentile(ordered, 50)),
                "p95_ms": _ms(percentile(ordered, 95)),
                "p99_ms": _ms(percentile(ordered, 99)),
                "status": counts,
                "errors": bad,
            }
            total += len(values)
            errors += bad
            n429 += counts.get("429", 0)
        rate = errors / total if total else 0.0
        out = {
            "clients": clients,
            "clients_started": self.started_clients,
            "clients_finished": self.finished_clients,
            "client_task_failures": self.client_failures,
            "total_requests": total,
            "error_requests": errors,
            "error_rate": rate,
            "count_429": n429,
            "simulated_timeouts": dict(self.simulated),
            "clients_lease_expired": len(self.lease_flagged),
            "clients_hold": len(self.hold_flagged),
            "endpoints": endpoints,
        }
        out["verdict"] = verdict(out)
        return out


def _ms(seconds):
    return None if seconds is None else round(seconds * 1000, 1)


def verdict(summary):
    """Same thresholds at every size; only >= 5000 clients may claim the 5,000-client bar."""
    n = summary["clients"]
    reasons = []
    renew = summary["endpoints"].get("renew")
    escrow_only = set(summary["endpoints"]) <= {"escrow"} and bool(summary["endpoints"])
    if not escrow_only:
        if not renew or renew["p99_ms"] is None:
            reasons.append("no renew requests were made")
        elif renew["p99_ms"] >= 1000:
            reasons.append(f"renew p99 {renew['p99_ms']} ms is not below 1000 ms")
    if summary["clients_lease_expired"]:
        reasons.append(f"{summary['clients_lease_expired']} clients saw lease-expired")
    if summary["clients_hold"]:
        reasons.append(f"{summary['clients_hold']} clients saw a hold signal")
    if summary["error_rate"] >= 0.001:
        reasons.append(f"error rate {summary['error_rate']:.4%} is not below 0.1%")
    if summary["count_429"]:
        reasons.append(f"{summary['count_429']} unintended 429 responses")
    if summary["client_task_failures"]:
        reasons.append(f"{summary['client_task_failures']} client tasks crashed")
    if reasons:
        return {"result": "FAIL", "label": f"FAIL at {n} clients: " + "; ".join(reasons), "reasons": reasons}
    if escrow_only:
        return {"result": "ESCROW_ONLY", "label": "escrow-only: renew thresholds not evaluated", "reasons": []}
    if n >= BAR_CLIENTS:
        return {"result": "PASS", "label": "PASS: 5,000-client bar met", "reasons": []}
    return {"result": "PASS", "label": f"PASS at {n} clients (this is NOT a 5,000-client proof)", "reasons": []}


def render(summary):
    lines = []
    for name, e in summary["endpoints"].items():
        lines.append(f"{name:<12} n={e['requests']:<8} rps={e['rps']} p50={e['p50_ms']}ms "
                     f"p95={e['p95_ms']}ms p99={e['p99_ms']}ms status={e['status']}")
    lines.append(f"errors {summary['error_requests']}/{summary['total_requests']} "
                 f"({summary['error_rate']:.4%}), 429s {summary['count_429']}, "
                 f"simulated timeouts {summary['simulated_timeouts']}")
    lines.append(f"clients with lease-expired {summary['clients_lease_expired']}, hold {summary['clients_hold']}")
    v = summary["verdict"]
    lines.append(v["label"])
    lines.append("Record OJ and database CPU from the host monitoring for this run; the harness cannot see them.")
    return "\n".join(lines)


def write_json(summary, path):
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
