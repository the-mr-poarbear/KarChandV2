import threading
import time


class AdaptiveRateLimiter:
    """
    Thread-safe rate limiter shared across worker threads.

    Starts with a small delay between requests. On a 429/403 or repeated
    timeouts, increases the delay (backs off). Never speeds back up
    automatically — designed to be conservative once it senses trouble,
    requiring restart to reset.
    """

    def __init__(self, initial_delay: float = 0.3, max_delay: float = 10.0):
        self._delay = initial_delay
        self._max_delay = max_delay
        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self.total_requests = 0
        self.total_backoffs = 0

    def wait(self):
        """Call before making a request — sleeps as needed to respect current delay."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            sleep_for = max(0.0, self._delay - elapsed)
            self._last_request_time = now + sleep_for
            self.total_requests += 1

        if sleep_for > 0:
            time.sleep(sleep_for)

    def report_failure(self, status_code: int | None = None, source: str = "unknown"):
        """Call when a request hits 429/403 or times out — increases delay."""
        with self._lock:
            old_delay = self._delay
            self._delay = min(self._delay * 2, self._max_delay)
            self.total_backoffs += 1
            new_delay = self._delay
            backoff_count = self.total_backoffs

        reason = f"HTTP {status_code}" if status_code else "timeout"
        hit_ceiling = " (at max delay ceiling)" if new_delay >= self._max_delay else ""
        print(
            f"🐢 Rate limiter backoff #{backoff_count} — source: {source} — reason: {reason} — "
            f"delay {old_delay:.2f}s → {new_delay:.2f}s{hit_ceiling}"
        )

    def current_delay(self) -> float:
        with self._lock:
            return self._delay