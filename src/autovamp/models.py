from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta


@dataclass
class PlaybackContext:
    position_samples: int
    is_vamping: bool
    is_paused: bool
    samplerate_hz: int


@dataclass
class PlaybackState:
    position_samples: int
    is_vamping: bool
    is_paused: bool
    current_vamp: Vamp | None
    playing: bool


class VampBehaviour(ABC):
    @property
    def name(self) -> str:
        return type(self).__name__.removesuffix("Vamp").upper()

    @abstractmethod
    def on_vamp_entry(self, vamp: Vamp, context: PlaybackContext) -> None:
        ...

    @abstractmethod
    def on_exit_requested(self, vamp: Vamp, context: PlaybackContext) -> None:
        ...

    @abstractmethod
    def on_vamp_exit(self, vamp: Vamp, context: PlaybackContext) -> None:
        ...


class JumpVamp(VampBehaviour):
    def on_vamp_entry(self, vamp: Vamp, context: PlaybackContext) -> None:
        pass

    def on_exit_requested(self, vamp: Vamp, context: PlaybackContext) -> None:
        context.position_samples = vamp.end_sample(context.samplerate_hz)
        context.is_vamping = False

    def on_vamp_exit(self, vamp: Vamp, context: PlaybackContext) -> None:
        context.position_samples = vamp.start_sample(context.samplerate_hz)


class ContinueVamp(VampBehaviour):
    def __init__(self) -> None:
        self._exit_requested: bool = False

    def on_vamp_entry(self, vamp: Vamp, context: PlaybackContext) -> None:
        pass

    def on_exit_requested(self, vamp: Vamp, context: PlaybackContext) -> None:
        self._exit_requested = True

    def on_vamp_exit(self, vamp: Vamp, context: PlaybackContext) -> None:
        if self._exit_requested:
            context.is_vamping = False
            self._exit_requested = False
        else:
            context.position_samples = vamp.start_sample(context.samplerate_hz)


class SafetyVamp(VampBehaviour):
    SAFETY_ITERATIONS: int = 1

    def __init__(self) -> None:
        self._remaining_iterations: int | None = None

    def on_vamp_entry(self, vamp: Vamp, context: PlaybackContext) -> None:
        pass

    def on_exit_requested(self, vamp: Vamp, context: PlaybackContext) -> None:
        self._remaining_iterations = self.SAFETY_ITERATIONS

    def on_vamp_exit(self, vamp: Vamp, context: PlaybackContext) -> None:
        if self._remaining_iterations is None:
            context.position_samples = vamp.start_sample(context.samplerate_hz)
        elif self._remaining_iterations > 0:
            context.position_samples = vamp.start_sample(context.samplerate_hz)
            self._remaining_iterations -= 1
        else:
            context.is_vamping = False
            self._remaining_iterations = None


class CaesuraVamp(VampBehaviour):
    def on_vamp_entry(self, vamp: Vamp, context: PlaybackContext) -> None:
        context.is_paused = True

    def on_exit_requested(self, vamp: Vamp, context: PlaybackContext) -> None:
        context.is_paused = False
        context.is_vamping = False

    def on_vamp_exit(self, vamp: Vamp, context: PlaybackContext) -> None:
        pass


@dataclass
class Vamp:
    start: timedelta
    end: timedelta
    behaviour: VampBehaviour

    def start_sample(self, samplerate_hz: int) -> int:
        return int(self.start.total_seconds() * samplerate_hz)

    def end_sample(self, samplerate_hz: int) -> int:
        return int(self.end.total_seconds() * samplerate_hz)

    @staticmethod
    def parse_timestamp(ts: str) -> timedelta:
        if "." in ts:
            time_part, _, ms_part = ts.rpartition(".")
            milliseconds = int(ms_part.ljust(3, "0"))
        else:
            time_part = ts
            milliseconds = 0

        parts = time_part.split(":")

        if len(parts) != 3:
            raise ValueError(f"Expected HH:MM:SS or HH:MM:SS.mmm, got: {ts}")

        hours, minutes, seconds = (int(p) for p in parts)

        return timedelta(
            hours=hours,
            minutes=minutes,
            seconds=seconds,
            milliseconds=milliseconds,
        )

    @staticmethod
    def format_timestamp(td: timedelta) -> str:
        total_seconds = td.total_seconds()
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds % 1) * 1000)

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
