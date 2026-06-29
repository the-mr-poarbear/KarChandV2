import threading
import time


class AdaptiveRateLimiter:
    """
    Thread-safe rate limiter shared across worker threads.

    Starts with a small delay between requests. On a 429/403 or repeated
    timeouts, increases the delay (backs off) AND pauses all threads for a
    flat cooldown period before any further requests are allowed. After a
    streak of consecutive successes, gradually decays the delay back down —
    never below the original initial_delay, and never faster than a slow,
    steady decay, so a brief good streak can't bounce it straight back to
    full speed only to get hit again immediately.

    The lowest delay ever successfully sustained (best_recovered_delay) is
    remembered. If the limiter fully recovers to initial_delay and then a
    fresh backoff happens, it jumps straight to best_recovered_delay instead
    of re-climbing slowly from initial_delay again — avoiding "rediscovering"
    a known-safe level through repeated slow doubling. Backoffs that happen
    while still above initial_delay (i.e. within an ongoing bad patch) still
    just double normally, so repeated failures in a single bad streak are
    not dampened.

    A single failure resets the success streak to zero, so recovery only
    progresses during a clean run with no backoffs in between.
    """

    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 6.0,
        recovery_threshold: int = 10,
        recovery_factor: float = 0.9,
        backoff_cooldown_seconds: float = 10.0,
    ):
        """
        recovery_threshold: number of consecutive successful requests needed
            before the delay decays one step. Higher = slower, more cautious
            recovery.
        recovery_factor: multiplier applied to the delay on each decay step
            (e.g. 0.9 = 10% faster each step). Must be < 1.0.
        backoff_cooldown_seconds: on every backoff, ALL threads are blocked
            from making further requests for this many seconds, on top of
            the normal per-request delay.
        """
        self._initial_delay = initial_delay
        self._delay = initial_delay
        self._max_delay = max_delay
        self._recovery_threshold = recovery_threshold
        self._recovery_factor = recovery_factor
        self._backoff_cooldown_seconds = backoff_cooldown_seconds

        # Lowest delay ever successfully sustained via recovery. Starts equal
        # to initial_delay (i.e. "no proven safe level below the floor yet").
        self._best_recovered_delay = initial_delay

        self._lock = threading.Lock()
        self._last_request_time = 0.0
        self._consecutive_successes = 0

        # Monotonic time until which ALL requests should block, set by a
        # backoff's cooldown. 0 means no active cooldown.
        self._cooldown_until = 0.0

        self.total_requests = 0
        self.total_backoffs = 0
        self.total_recoveries = 0

    def wait(self):
        """
        Call before making a request — sleeps as needed to respect both the
        current per-request delay AND any active backoff cooldown.
        """
        with self._lock:
            now = time.monotonic()

            # Respect an active cooldown first — every thread checks this,
            # so a backoff pauses ALL requests, not just the thread that hit it.
            cooldown_remaining = max(0.0, self._cooldown_until - now)

            elapsed = now - self._last_request_time
            delay_remaining = max(0.0, self._delay - elapsed)

            sleep_for = max(cooldown_remaining, delay_remaining)
            self._last_request_time = now + sleep_for
            self.total_requests += 1

        if sleep_for > 0:
            time.sleep(sleep_for)

    def report_success(self):
        """
        Call after a request completes successfully (2xx, not a timeout).
        Tracks a consecutive-success streak; once it reaches
        recovery_threshold, decays the delay one step, resets the streak,
        and updates best_recovered_delay if this is the lowest proven level
        yet.

        Calling this is optional but required for recovery to ever happen —
        without it, the limiter behaves like the old version (backs off,
        never speeds back up).
        """
        with self._lock:
            if self._delay <= self._initial_delay:
                # Already at the floor - nothing to recover, no need to track streaks.
                self._consecutive_successes = 0
                return

            self._consecutive_successes += 1
            if self._consecutive_successes < self._recovery_threshold:
                return

            old_delay = self._delay
            self._delay = max(self._initial_delay, self._delay * self._recovery_factor)
            self._consecutive_successes = 0
            self.total_recoveries += 1
            new_delay = self._delay

            if new_delay < self._best_recovered_delay:
                self._best_recovered_delay = new_delay

        print(
            f"🐇 Rate limiter recovery #{self.total_recoveries} — "
            f"delay {old_delay:.2f}s → {new_delay:.2f}s"
        )

    def report_failure(self, status_code: int | None = None, source: str = "unknown"):
        """
        Call when a request hits 429/403 or times out — increases delay and
        triggers a flat cooldown pause for all threads.

        If the delay was already back at initial_delay (a full recovery had
        completed, then a fresh failure happened), jump straight to
        best_recovered_delay instead of starting the doubling climb over
        from initial_delay. Otherwise (already mid-backoff, within the same
        bad patch), double normally.
        """
        with self._lock:
            old_delay = self._delay

            if self._delay <= self._initial_delay and self._best_recovered_delay > self._initial_delay:
                # Fresh failure after a full recovery - jump to the last
                # proven-safe level rather than re-climbing from the floor.
                self._delay = min(self._best_recovered_delay, self._max_delay)
            else:
                self._delay = min(self._delay * 2, self._max_delay)

            self.total_backoffs += 1
            self._consecutive_successes = 0  # any failure breaks the recovery streak
            backoff_count = self.total_backoffs
            new_delay = self._delay

            # Flat cooldown: block every thread's next wait() until this
            # many seconds from now, regardless of the per-request delay.
            self._cooldown_until = time.monotonic() + self._backoff_cooldown_seconds

        reason = f"HTTP {status_code}" if status_code else "timeout"
        hit_ceiling = " (at max delay ceiling)" if new_delay >= self._max_delay else ""
        print(
            f"🐢 Rate limiter backoff #{backoff_count} — source: {source} — reason: {reason} — "
            f"delay {old_delay:.2f}s → {new_delay:.2f}s{hit_ceiling} — "
            f"cooling down for {self._backoff_cooldown_seconds:.0f}s"
        )

    def current_delay(self) -> float:
        with self._lock:
            return self._delay

    def best_recovered_delay(self) -> float:
        with self._lock:
            return self._best_recovered_delay