"""Terminal-based user interface for AutoVamp.

Renders a live-updating progress bar and status display and
handles keyboard input for controlling playback.
"""

from __future__ import annotations
from datetime import timedelta
import os
import select
import sys
import time
import threading
from .. import __version__
from ..engine import VampEngine
from ..models import format_timestamp

# Keyboard polling interval in seconds. Smaller values are
# more responsive but use more CPU.
KEY_POLL_INTERVAL_SECONDS: float = 0.1

# Status line redraw interval in seconds.
CLI_REFRESH_INTERVAL_SECONDS: float = 0.05

# ANSI escape codes for terminal styling.
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"

# Progress bar width in characters.
UI_WIDTH_CHARS = 44

# Horizontal and vertical padding for the UI layout.
PAD_X = " " * 2
PAD_Y = "\n"

# Key bindings: (raw_bytes, label, description, action).
# Entries with an empty label are hidden in the header but
# still dispatched. Action is a name or "seek:<seconds>".
KEY_BINDINGS: list[tuple[tuple[bytes, ...], str, str, str]] = [
	((b" ",), "SPACE", "play/pause", "play_pause"),
	((b"\r", b"\n"), "ENTER", "exit cue", "exit_cue"),
	((b"q",), "Q", "quit", "quit"),
	((b"\x1b[D",), "\u2190/\u2192", "\u00b15s", "seek:-5"),
	((b"\x1b[C",), "", "", "seek:5"),
	((b"\x1b[1;3D",), "ALT+\u2190/\u2192", "\u00b11s", "seek:-1"),
	((b"\x1b[1;3C",), "", "", "seek:1"),
	((b"\x1b[1;5D",), "CTRL+\u2190/\u2192", "\u00b130s", "seek:-30"),
	((b"\x1b[1;5C",), "", "", "seek:30"),
	((b"\x1b",), "ESC", "restart", "restart"),
]


class CliApp:
	"""Terminal interface for audio playback with cues.

	Renders a live status line with progress bar and cue state,
	and handles keyboard input. Reads from a VampEngine which
	manages the actual audio playback and cue logic.
	"""

	def __init__(
			self, engine: VampEngine, filename: str = "",
	) -> None:
		"""Initialise the CLI application.

		Args:
			engine: The VampEngine that handles audio playback
				and cue behaviour.
			filename: Path to the audio file, shown in the
				header. If empty, the file label is omitted.
		"""
		self._engine: VampEngine = engine
		self._filename: str = filename
		self._key_thread: threading.Thread | None = None

		# Terminal lines occupied by the previous render,
		# used to move the cursor back before overwriting.
		self._rendered_lines: int = 0
		self._cue_char_colours: dict[int, str] = (
			self._precompute_cue_colours()
		)

	def run(self) -> None:
		"""Run the CLI from start to finish.

		Prints the header, starts the key reader thread, begins
		playback, and blocks on the status loop until done.
		"""
		self._print_header()
		self._start_key_reader()
		self._engine.play()
		self._status_loop()
		self._engine.stop()

		if self._key_thread is not None:
			self._key_thread.join(timeout=1.0)

		print(f"{PAD_Y}{PAD_X}{GREEN}{BOLD}Done.{RESET}{PAD_Y}")

	def _print_header(self) -> None:
		"""Print the header: title, file metadata, cue list,
		and keyboard controls."""
		title = f"{BOLD}{CYAN}AutoVamp{RESET} {DIM}(v{__version__}){RESET}"
		sep = f"{PAD_X}{'─' * UI_WIDTH_CHARS}"

		file_label = ""
		if self._filename:
			basename = os.path.basename(self._filename)
			file_label = f"{PAD_X}{DIM}File:{RESET} {basename}"

		duration_seconds = self._engine.duration_seconds
		duration_label = (
			f"{PAD_X}{DIM}Duration:{RESET} {duration_seconds:.1f}s"
		)
		samplerate_hz = self._engine.samplerate_hz
		rate_label = (
			f"{PAD_X}{DIM}Sample rate:{RESET} {samplerate_hz}Hz"
		)

		print(PAD_Y, end="")
		print(f"{PAD_X}{title}")
		print(sep)

		if file_label:
			print(file_label)

		print(duration_label)
		print(rate_label)
		print(sep)

		cues = self._engine.cues

		for i, cue in enumerate(cues, 1):
			colour = cue.behaviour.colour
			name = str(cue.behaviour)
			start = format_timestamp(cue.start_time)
			left = f"({i}) {name}"

			if cue.end_time is not None:
				end = format_timestamp(cue.end_time)
				right = f"{start}\u2013{end}"
			else:
				right = start

			gap = UI_WIDTH_CHARS - len(left) - len(right)

			print(
				f"{PAD_X}{colour}{left}{RESET}"
				f"{' ' * gap}"
				f"{DIM}{right}{RESET}"
			)

		print(sep)

		for _, label, desc, _ in KEY_BINDINGS:
			if label:
				print(f"{PAD_X}{DIM}{label}:{RESET} {desc}")

		print(sep)
		print(PAD_Y, end="")

	def _start_key_reader(self) -> None:
		"""Start a daemon thread that listens for keyboard input.

		On Windows, uses msvcrt polling. On Unix, switches stdin
		to raw mode via termios and restores it on exit.
		"""

		# Build a dispatch map: raw bytes -> action string.
		dispatch: dict[bytes, str] = {}
		for raw_seqs, _, _, action in KEY_BINDINGS:
			for raw in raw_seqs:
				dispatch[raw] = action

		def handle_input(data: bytes) -> bool:
			"""Process raw input bytes from the terminal.

			Args:
				data: Raw bytes received from stdin.

			Returns:
				True to keep listening, False to quit.
			"""
			action = dispatch.get(data)

			if action is None:
				return True
			elif action == "play_pause":
				self._engine.toggle_pause()
			elif action == "exit_cue":
				self._engine.exit_current_cue()
			elif action == "quit":
				self._engine.stop()
				return False
			elif action == "restart":
				self._engine.seek(-1e9)
			elif action.startswith("seek:"):
				self._engine.seek(float(action.split(":")[1]))

			return True

		if sys.platform == "win32":
			def read_keys() -> None:
				import msvcrt
				while not self._engine.done.is_set():
					if msvcrt.kbhit():
						ch = msvcrt.getwch()
						if not handle_input(ch.encode()):
							return
					else:
						time.sleep(KEY_POLL_INTERVAL_SECONDS)
		else:
			def read_keys() -> None:
				import termios
				import tty

				fd = sys.stdin.fileno()
				old_settings = termios.tcgetattr(fd)

				try:
					tty.setraw(fd)

					while not self._engine.done.is_set():
						# Poll with a timeout so we can check
						# if the engine has finished.
						ready, _, _ = select.select(
							[fd], [], [],
							KEY_POLL_INTERVAL_SECONDS,
						)

						if ready:
							# Read up to 32 bytes so multi-byte
							# escape sequences arrive as one chunk.
							data = os.read(fd, 32)
							if not data:
								return
							if not handle_input(data):
								return
				finally:
					termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

		self._key_thread = threading.Thread(
			target=read_keys, daemon=True,
		)

		self._key_thread.start()

	def _precompute_cue_colours(self) -> dict[int, str]:
		"""Map each progress bar character position to a colour.

		Positions outside any cue region are not included;
		callers should default to CYAN.

		Returns:
			A dict mapping character index to ANSI colour code.
		"""
		duration = self._engine.duration_seconds

		if duration <= 0:
			return {}

		pos_colours: dict[int, str] = {}

		for cue in self._engine.cues:
			colour = cue.behaviour.colour
			cue_start_frac = (cue.start_time.total_seconds() / duration)

			if cue.end_time is not None:
				cue_end_frac = (cue.end_time.total_seconds() / duration)
			else:
				cue_end_frac = cue_start_frac

			char_start = int(cue_start_frac * UI_WIDTH_CHARS)
			char_end = max(char_start, int(cue_end_frac * UI_WIDTH_CHARS))

			for i in range(char_start, char_end + 1):
				if 0 <= i < UI_WIDTH_CHARS:
					pos_colours[i] = colour

		return pos_colours

	def _build_progress_bar(self, fraction: float, ) -> str:
		"""Build a coloured text progress bar.

		Each character maps to a time range. Cue regions use
		their behaviour's colour; other positions use cyan. The
		played portion is filled blocks, the rest dimmed shading.

		Args:
			fraction: 0.0 to 1.0 representing playback progress.

		Returns:
			A string containing the progress bar with ANSI
			colour codes, ready for printing.
		"""
		if self._engine.duration_seconds <= 0:
			return f"{DIM}{'░' * UI_WIDTH_CHARS}{RESET}"

		filled_pos = int(fraction * UI_WIDTH_CHARS)
		filled_pos = max(0, min(filled_pos, UI_WIDTH_CHARS))

		# Build character by character, batching runs of the
		# same style to reduce escape codes.
		bar = ""
		prev_code = ""

		for i in range(UI_WIDTH_CHARS):
			is_filled = i < filled_pos
			colour = self._cue_char_colours.get(i, CYAN)
			if is_filled:
				code = colour
				char = "█"
			else:
				code = f"{colour}{DIM}"
				char = "░"
			if code != prev_code:
				bar += code
				prev_code = code
			bar += char

		return bar + RESET

	def _status_loop(self) -> None:
		"""Redraw the status line until playback ends.

		Polls the engine state at a regular interval and
		overwrites the same terminal lines each frame.
		"""
		while not self._engine.done.is_set():
			state = self._engine.state

			# Convert playhead position to a timestamp.
			position_time = timedelta(
				seconds=(state.position_samples / self._engine.samplerate_hz)
			)

			total_duration = timedelta(seconds=self._engine.duration_seconds)

			position_str = format_timestamp(position_time)
			total_str = format_timestamp(total_duration)

			# Progress fraction for the bar fill level.
			duration_seconds = self._engine.duration_seconds

			if duration_seconds > 0:
				progress_fraction = (
						state.position_samples
						/ (self._engine.samplerate_hz * duration_seconds)
				)
			else:
				progress_fraction = 0.0

			progress_bar = self._build_progress_bar(progress_fraction)

			# Show cue region timestamps while inside a cue.
			cue_indicator = ""
			cue_colour = CYAN

			if state.in_cue and state.current_cue is not None:
				cue_colour = state.current_cue.behaviour.colour
				cue_start_str = format_timestamp(
					state.current_cue.start_time,
				)

				if state.current_cue.end_time is not None:
					cue_end_str = format_timestamp(
						state.current_cue.end_time,
					)
					cue_range = (
						f"{cue_start_str}"
						f"\u2013{cue_end_str}"
					)
				else:
					cue_range = cue_start_str

				active_msg = state.current_cue.behaviour.active_message
				cue_indicator = (
					f"  {cue_colour}{BOLD}{active_msg}{RESET}"
					f" {DIM}{cue_range}{RESET}"
				)

			status_message = None
			if state.current_cue is not None and state.in_cue:
				status_message = state.current_cue.behaviour.status_message

			time_display = (
				f"{BOLD}{position_str}{RESET}"
				f" {DIM}/{RESET} {total_str}"
			)

			lines = [
				f"{PAD_X}{progress_bar}  {time_display}"
				f"{cue_indicator}",
			]
			if status_message is not None:
				second_line = f"{PAD_X}{cue_colour}{status_message}{RESET}"
			else:
				second_line = ""

			lines.append("")
			lines.append(second_line)

			# Move cursor back up and overwrite previous lines.
			output_lines = ""
			if self._rendered_lines > 1:
				output_lines += f"\033[{self._rendered_lines - 1}A"

			for i, line in enumerate(lines):
				if i > 0:
					output_lines += "\n"
				output_lines += f"\r{line}\033[K"

			self._rendered_lines = len(lines)
			print(output_lines, end="", flush=True)

			time.sleep(CLI_REFRESH_INTERVAL_SECONDS)
