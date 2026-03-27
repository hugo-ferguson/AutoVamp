"""Data models, timestamp utilities, and cue behaviour definitions.

Cues are timed regions in an audio file that trigger specific
behaviours during playback. Some cues vamp (loop repeatedly),
while others pause or jump past the region.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta


def parse_timestamp(ts: str) -> timedelta:
	"""Parse a timestamp string in HH:MM:SS or HH:MM:SS.mmm format.

	The millisecond portion is optional. If provided, it is
	right-padded to three digits (so "1.5" means 500ms).

	Args:
		ts: The timestamp string to parse.

	Returns:
		A timedelta representing the parsed time.

	Raises:
		ValueError: If the string is not in HH:MM:SS format.
	"""
	if "." in ts:
		time_part, _, ms_part = ts.rpartition(".")
		milliseconds = int(ms_part.ljust(3, "0")[:3])
	else:
		time_part = ts
		milliseconds = 0

	parts = time_part.split(":")

	if len(parts) == 3:
		hours, minutes, seconds = (int(p) for p in parts)
	else:
		raise ValueError(
			f"Expected HH:MM:SS (with optional .mmm), "
			f"got: {ts}"
		)

	return timedelta(
		hours=hours,
		minutes=minutes,
		seconds=seconds,
		milliseconds=milliseconds,
	)


def format_timestamp(td: timedelta) -> str:
	"""Format a timedelta as an HH:MM:SS.mmm string.

	Args:
		td: The timedelta to format.

	Returns:
		A formatted timestamp string.
	"""
	total_seconds = td.total_seconds()
	hours = int(total_seconds // 3600)
	minutes = int((total_seconds % 3600) // 60)
	seconds = int(total_seconds % 60)
	milliseconds = int((total_seconds % 1) * 1000)

	return (
		f"{hours:02d}:{minutes:02d}:{seconds:02d}"
		f".{milliseconds:03d}"
	)


@dataclass
class PlaybackContext:
	"""Mutable playback state snapshot passed to cue behaviours.

	The engine creates this from its internal state, passes it
	to a behaviour method, and applies any changes back. This
	avoids giving behaviours direct access to the engine.
	"""

	position_samples: int
	in_cue: bool
	is_paused: bool
	samplerate_hz: int


@dataclass
class PlaybackState:
	"""Read-only playback state snapshot used by the CLI.

	Unlike PlaybackContext, this is not passed to behaviours
	and modifications have no effect on the engine.
	"""

	position_samples: int
	in_cue: bool
	is_paused: bool
	is_playing: bool
	current_cue: Cue | None


_MAGENTA = "\033[35m"
_BLUE = "\033[34m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"


class CueBehaviour(ABC):
	"""Base class for cue behaviours.

	Defines what happens when the playhead enters a cue region,
	when the user requests an exit, and when the playhead reaches
	the cue's end boundary.
	"""

	@abstractmethod
	def __str__(self) -> str:
		...

	@property
	@abstractmethod
	def colour(self) -> str:
		"""ANSI colour code for this behaviour in the UI."""
		...

	@property
	def active_message(self) -> str:
		"""Label shown in the status line while the cue is
		active. Defaults to 'VAMPING'."""
		return "VAMPING"

	@property
	def status_message(self) -> str | None:
		"""Live status message polled each frame by the CLI.
		Returns None when there is nothing to show."""
		return None

	@abstractmethod
	def on_cue_entry(self, cue: Cue, context: PlaybackContext, ) -> None:
		"""Called when the playhead first enters the cue region.

		Args:
			cue: The cue whose region has been entered.
			context: Mutable playback context. Changes are
				applied back to the engine.
		"""
		...

	@abstractmethod
	def on_exit_requested(self, cue: Cue, context: PlaybackContext) -> None:
		"""Called when the user requests to exit the cue.

		Args:
			cue: The cue the user wants to exit.
			context: Mutable playback context. Changes are
				applied back to the engine.
		"""
		...

	@abstractmethod
	def on_cue_exit(self, cue: Cue, context: PlaybackContext) -> None:
		"""Called when the playhead reaches the end of the cue.

		Args:
			cue: The cue whose end boundary was reached.
			context: Mutable playback context. Changes are
				applied back to the engine.
		"""
		...


class Jump(CueBehaviour):
	"""Jumps past the cue immediately on exit request.
	Loops back to the start if the end is reached naturally."""

	def __str__(self) -> str:
		return "Jump"

	@property
	def colour(self) -> str:
		return _YELLOW

	def on_cue_entry(self, cue: Cue, context: PlaybackContext) -> None:
		pass

	def on_exit_requested(self, cue: Cue, context: PlaybackContext) -> None:
		# Jump past the cue region.
		context.position_samples = cue.end_sample(context.samplerate_hz)
		context.in_cue = False

	def on_cue_exit(self, cue: Cue, context: PlaybackContext) -> None:
		# No exit request, so loop back.
		context.position_samples = cue.start_sample(context.samplerate_hz)


class Continue(CueBehaviour):
	"""Vamps a region, finishing the current iteration before exiting.

	If repetitions is set, the cue loops that many times then
	exits automatically. Pressing ENTER queues one more loop.
	If repetitions is not set, the cue loops indefinitely until
	ENTER is pressed, then exits after the current iteration.

	Args:
		repetitions: Number of times to loop before exiting.
			If None, loops indefinitely until exit is requested.
	"""

	def __init__(self, repetitions: int | None = None) -> None:
		self._repetitions: int | None = repetitions
		self._remaining: int = 0
		self._exit_requested: bool = False

	def __str__(self) -> str:
		return "Continue"

	@property
	def colour(self) -> str:
		return _GREEN

	def on_cue_entry(self, cue: Cue, context: PlaybackContext, ) -> None:
		self._exit_requested = False
		if self._repetitions is not None:
			self._remaining = self._repetitions

	@property
	def status_message(self) -> str | None:
		if self._repetitions is not None:
			if self._remaining > 0:
				return f"VAMPING ({self._remaining} remaining)"
			return "EXITING VAMP"
		if self._exit_requested:
			return "EXITING VAMP"
		return None

	def on_exit_requested(self, cue: Cue, context: PlaybackContext, ) -> None:
		if self._repetitions is not None:
			self._remaining += 1
		else:
			self._exit_requested = True

	def on_cue_exit(self, cue: Cue, context: PlaybackContext, ) -> None:
		if self._repetitions is not None:
			if self._remaining > 0:
				self._remaining -= 1
				context.position_samples = cue.start_sample(context.samplerate_hz)
			else:
				context.in_cue = False
		elif self._exit_requested:
			context.in_cue = False
			self._exit_requested = False
		else:
			context.position_samples = cue.start_sample(context.samplerate_hz)


class Safety(CueBehaviour):
	"""Exits automatically unless the user requests more loops.

	Playback continues past the cue by default. Each press of
	ENTER during the cue queues one additional loop iteration.
	"""

	def __init__(self) -> None:
		self._extra_loops: int = 0
		self._activated: bool = False

	def __str__(self) -> str:
		return "Safety"

	@property
	def colour(self) -> str:
		return _MAGENTA

	@property
	def status_message(self) -> str | None:
		if not self._activated:
			return None
		if self._extra_loops > 0:
			return f"REPEATING VAMP (+{self._extra_loops})"

		return "EXITING VAMP"

	def on_cue_entry(self, cue: Cue, context: PlaybackContext) -> None:
		self._extra_loops = 0
		self._activated = False

	def on_exit_requested(self, cue: Cue, context: PlaybackContext) -> None:
		self._extra_loops += 1
		self._activated = True

	def on_cue_exit(self, cue: Cue, context: PlaybackContext) -> None:
		if self._extra_loops > 0:
			self._extra_loops -= 1
			context.position_samples = cue.start_sample(context.samplerate_hz)
		else:
			context.in_cue = False


class Caesura(CueBehaviour):
	"""Pauses playback upon entering the cue region.

	Waits for the user to press ENTER, then resumes and
	continues past the cue. Named after the musical term
	for a pause or break.
	"""

	def __str__(self) -> str:
		return "Caesura"

	@property
	def colour(self) -> str:
		return _BLUE

	@property
	def active_message(self) -> str:
		return "PAUSED"

	def on_cue_entry(self, cue: Cue, context: PlaybackContext) -> None:
		context.is_paused = True

	def on_exit_requested(self, cue: Cue, context: PlaybackContext) -> None:
		context.is_paused = False
		context.in_cue = False

	def on_cue_exit(self, cue: Cue, context: PlaybackContext) -> None:
		pass


@dataclass
class Cue:
	"""A cue region: a start time, optional end time, and a
	behaviour that controls what happens during playback.

	For point-in-time behaviours like Caesura, end_time can be
	omitted and defaults to start_time.
	"""

	start_time: timedelta
	behaviour: CueBehaviour
	end_time: timedelta | None = None

	def start_sample(self, samplerate_hz: int) -> int:
		"""Convert start time to a sample index.

		Args:
			samplerate_hz: The audio sample rate in Hertz.

		Returns:
			The sample index at the start of this cue.
		"""
		return int(self.start_time.total_seconds() * samplerate_hz)

	def end_sample(self, samplerate_hz: int) -> int:
		"""Convert end time to a sample index. Falls back to
		start_sample for point-in-time cues.

		Args:
			samplerate_hz: The audio sample rate in Hertz.

		Returns:
			The sample index at the end of this cue.
		"""
		t = self.end_time if self.end_time is not None else self.start_time
		return int(t.total_seconds() * samplerate_hz)
