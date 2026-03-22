from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta


def parse_timestamp(ts: str) -> timedelta:
	"""Parse a timestamp string in HH:MM:SS or HH:MM:SS.mmm format.

	The millisecond portion is optional and treated as a
	decimal fraction of a second (so "0:00:01.5" is 1500ms
	and "0:00:01.55" is 1550ms). If provided, it is
	right-padded with zeroes to three digits.

	Args:
		ts (str): The timestamp string to parse.

	Returns:
		timedelta: A timedelta representing the parsed time.

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
	"""Format a timedelta as an HH:MM:SS.mmm timestamp string.

	Args:
		td (timedelta): The timedelta to format.

	Returns:
		str: A formatted timestamp string.
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
	"""Mutable snapshot of playback state passed to vamp behaviours.

	When the engine needs to let a vamp behaviour modify the
	playback state (for example, to rewind the playhead or pause
	playback), it creates a PlaybackContext from its current
	internal state, passes it to the behaviour method, and then
	applies any changes the behaviour made back to the engine.
	This avoids giving behaviours direct access to the engine's
	internals.
	"""

	position_samples: int
	is_vamping: bool
	is_paused: bool
	samplerate_hz: int


@dataclass
class PlaybackState:
	"""Read-only snapshot of the engine's playback state.

	This is used by the CLI to display the current position, vamp
	status, and other information without needing to hold the
	engine's lock. Unlike PlaybackContext, this dataclass is not
	passed to behaviours and modifications to it have no effect on
	the engine.
	"""

	position_samples: int
	is_vamping: bool
	is_paused: bool
	is_playing: bool
	current_vamp: Vamp | None


class VampBehaviour(ABC):
	"""Base class for all vamp behaviours.

	A vamp behaviour defines what happens when the playhead enters
	a vamp region, when the user requests to exit the vamp, and
	when the playhead reaches the end of the vamp region. Subclasses
	override these three hooks to implement different looping and
	exit strategies.
	"""

	@property
	def status_message(self) -> str | None:
		"""A live status message to display in the UI.

		Polled each frame by the CLI. Returns None when there
		is nothing to show. Subclasses override this to provide
		context-specific messages.
		"""
		return None

	@abstractmethod
	def on_vamp_entry(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		"""Called when the playhead first enters the vamp region.

		Behaviours can use this to set up initial state, pause
		playback, or perform any other action needed at the start
		of the vamp.

		Args:
			vamp (Vamp): The vamp whose region has just been
				entered.
			context (PlaybackContext): The mutable playback
				context. Any changes made to this object will be
				applied back to the engine.
		"""
		...

	@abstractmethod
	def on_exit_requested(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		"""Called when the user requests to exit the vamp.

		The behaviour decides how to handle the request. Some
		behaviours exit immediately by jumping past the vamp
		region, others set a flag to exit after the current
		iteration completes.

		Args:
			vamp (Vamp): The vamp the user wants to exit.
			context (PlaybackContext): The mutable playback
				context. Any changes made to this object will be
				applied back to the engine.
		"""
		...

	@abstractmethod
	def on_vamp_exit(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		"""Called when the playhead reaches the end of the vamp.

		The behaviour decides whether to loop back to the start
		of the vamp or allow playback to continue past the vamp
		region.

		Args:
			vamp (Vamp): The vamp whose end boundary has been
				reached.
			context (PlaybackContext): The mutable playback
				context. Any changes made to this object will be
				applied back to the engine.
		"""
		...


class JumpVamp(VampBehaviour):
	"""Jumps past the vamp region when the user requests an exit.

	On exit request, the playhead is moved to the end of the vamp
	so that playback continues from just after the vamp region.
	When the playhead reaches the end of the vamp naturally
	(without an exit request), it loops back to the start.
	"""

	def on_vamp_entry(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		pass

	def on_exit_requested(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		# Move the playhead to the end of the vamp region and
		# mark it as no longer active, so playback continues
		# past the vamp.
		context.position_samples = vamp.end_sample(
			context.samplerate_hz
		)
		context.is_vamping = False

	def on_vamp_exit(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		# The playhead reached the end of the vamp without an
		# exit request, so loop back to the start.
		context.position_samples = vamp.start_sample(
			context.samplerate_hz
		)


class ContinueVamp(VampBehaviour):
	"""Finishes the current loop iteration before exiting.

	When the user requests an exit, this behaviour does not
	interrupt the current iteration. Instead, it sets a flag so
	that the next time the playhead reaches the end of the vamp
	region, playback continues past the vamp rather than looping
	back.
	"""

	def __init__(self) -> None:
		self._exit_requested: bool = False

	def on_vamp_entry(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		pass

	@property
	def status_message(self) -> str | None:
		if self._exit_requested:
			return "EXITING VAMP"
		return None

	def on_exit_requested(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		self._exit_requested = True

	def on_vamp_exit(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		if self._exit_requested:
			# The user previously requested an exit, so stop
			# vamping and let playback continue past the vamp.
			context.is_vamping = False
			self._exit_requested = False
		else:
			# No exit was requested, so loop back to the start.
			context.position_samples = vamp.start_sample(
				context.samplerate_hz
			)


class Safety(VampBehaviour):
	"""Exits the vamp by default unless the user requests more time.

	When the playhead reaches the end of the vamp region, playback
	continues past the vamp automatically. If the user presses
	SPACE during the vamp, one additional loop is guaranteed before
	exiting. Pressing SPACE again adds another loop, and so on.
	"""

	def __init__(self) -> None:
		self._extra_loops: int = 0
		self._activated: bool = False

	@property
	def status_message(self) -> str | None:
		if not self._activated:
			return None
		if self._extra_loops > 0:
			return f"REPEATING VAMP (+{self._extra_loops})"
		return "EXITING VAMP"

	def on_vamp_entry(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		self._extra_loops = 0
		self._activated = False

	def on_exit_requested(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		# Each press adds one more guaranteed loop.
		self._extra_loops += 1
		self._activated = True

	def on_vamp_exit(
			self, vamp: Vamp, context: PlaybackContext,

	) -> None:
		if self._extra_loops > 0:
			self._extra_loops -= 1
			context.position_samples = vamp.start_sample(
				context.samplerate_hz
			)
		else:
			context.is_vamping = False


class CaesuraVamp(VampBehaviour):
	"""Pauses playback upon entering the vamp region.

	The audio stops and waits for the user to press SPACE, at
	which point playback resumes and continues past the vamp.
	This is useful for rehearsal marks or fermatas where the
	performer needs to wait for a cue. The name comes from the
	musical term "caesura", which indicates a pause or break in
	the music.
	"""

	def on_vamp_entry(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		# Pause playback as soon as the vamp region is entered.
		# The engine will output silence until the user requests
		# an exit.
		context.is_paused = True

	def on_exit_requested(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		# Resume playback and exit the vamp so the audio
		# continues from where it was paused.
		context.is_paused = False
		context.is_vamping = False

	def on_vamp_exit(
			self, vamp: Vamp, context: PlaybackContext,
	) -> None:
		pass


@dataclass
class Vamp:
	"""A vamp region defined by a start time, end time, and behaviour.

	A vamp is a section of audio that loops repeatedly until the
	user chooses to move on. The behaviour determines exactly how
	the looping and exiting works (for example, immediate jump,
	finish the current iteration, or play a safety iteration
	before exiting).
	"""

	start_time: timedelta
	end_time: timedelta
	behaviour: VampBehaviour

	def start_sample(self, samplerate_hz: int) -> int:
		"""Convert the start timestamp to a sample index.

		Args:
			samplerate_hz (int): The sample rate of the audio
				file in Hertz.

		Returns:
			int: The sample index corresponding to the start of
				this vamp.
		"""
		return int(self.start_time.total_seconds() * samplerate_hz)

	def end_sample(self, samplerate_hz: int) -> int:
		"""Convert the end timestamp to a sample index.

		Args:
			samplerate_hz (int): The sample rate of the audio
				file in Hertz.

		Returns:
			int: The sample index corresponding to the end of
				this vamp.
		"""
		return int(self.end_time.total_seconds() * samplerate_hz)
