import random

RENEW_INTERVAL = 20.0
JITTER = 0.2
BACKOFF_STEPS = (2.0, 4.0, 8.0, 16.0)
BACKOFF_CAP = 20.0
RETRY_AFTER_CAP = 30.0
HAND_IN_FOLLOW_UPS = (0.0, 2.0, 5.0)
LEASE_SECONDS = 90.0


def parse_retry_after(value):
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 and seconds != float("inf") else None


def is_overloaded(status, transport_failed):
    if transport_failed or status is None:
        return True
    return status == 429 or 500 <= status <= 599


class RenewSchedule:
    """Same numbers as mac NativeRenewSchedule.swift, in unscaled seconds."""

    def __init__(self, rand=random.random):
        self.rand = rand
        self.failures = 0

    def interval(self):
        return RENEW_INTERVAL * (1 + JITTER * (2 * min(max(self.rand(), 0), 1) - 1))

    def next(self, status, transport_failed=False, retry_after=None):
        if is_overloaded(status, transport_failed):
            self.failures += 1
            step = min(BACKOFF_STEPS[min(self.failures, len(BACKOFF_STEPS)) - 1], BACKOFF_CAP)
            wait = min(max(self.rand(), 0), 1) * step
            if retry_after is None:
                return wait
            return max(wait, min(retry_after, RETRY_AFTER_CAP))
        if status is not None and 200 <= status < 300:
            self.failures = 0
        return self.interval()
