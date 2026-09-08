import threading
import uuid


class TurnGuard:
    """Monotonic conversation generation used to fence stale async work."""

    def __init__(self):
        self._lock = threading.Lock()
        self.generation = 0
        self.turn_id: str | None = None
        self.session_id: str | None = None

    def set_session(self, session_id: str) -> None:
        with self._lock:
            self.session_id = session_id

    def new_turn(self) -> tuple[int, str]:
        with self._lock:
            self.generation += 1
            self.turn_id = str(uuid.uuid4())
            return self.generation, self.turn_id

    def snapshot(self) -> int:
        with self._lock:
            return self.generation

    def is_stale(self, generation: int) -> bool:
        with self._lock:
            return generation != self.generation

    def fence(self, generation: int) -> bool:
        """Return True when a result is stale. Never mutates current state."""
        return self.is_stale(generation)

    def invalidate(self) -> tuple[int, str]:
        return self.new_turn()


global_guard = TurnGuard()