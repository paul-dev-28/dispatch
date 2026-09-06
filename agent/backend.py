import asyncio
import os


class FieldBackend:
    """Deterministic demo backend. The delay simulates a slow field API."""

    def __init__(self, delay_s: float | None = None, fast_delay_s: float = 0.15):
        self.delay_s = delay_s if delay_s is not None else float(os.getenv("SIMULATED_TOOL_DELAY_S", "4"))
        self.fast_delay_s = fast_delay_s

    async def _delay(self, seconds: float) -> None:
        # asyncio.sleep is intentionally cancellable; real HTTP clients should
        # propagate the cancellation signal to the underlying request.
        await asyncio.sleep(seconds)

    async def lookup_part_status(self, part_number: str) -> dict:
        await self._delay(self.delay_s)
        return {"part_number": part_number, "status": "in stock", "bay": 4, "units": 12}

    async def check_bay_status(self, bay: str) -> dict:
        await self._delay(self.fast_delay_s)
        return {"bay": bay, "status": "clear"}

    async def get_next_job(self) -> dict:
        await self._delay(self.fast_delay_s)
        return {"job": "compressor inspection", "time": "10:30"}

    async def log_inspection(self, unit: str = "compressor unit", result: str = "passed") -> dict:
        await self._delay(self.fast_delay_s)
        return {"unit": unit, "result": result, "logged": True}

    async def get_site_weather(self) -> dict:
        await self._delay(self.fast_delay_s)
        return {"temperature_c": 28, "condition": "clear"}
