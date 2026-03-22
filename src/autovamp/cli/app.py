"""Terminal-based user interface for AutoVamp.

This module provides the CliApp class, which renders a
live-updating progress bar and status display in the terminal
and handles keyboard input for controlling playback and vamp
interactions.
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

# How often (in seconds) the keyboard input thread checks for
# new keypresses. A smaller value makes the interface more
# responsive but uses more CPU.
KEY_POLL_INTERVAL_SECONDS: float = 0.1

# ANSI escape codes used to style terminal output.
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"

# The number of characters wide the progress bar is rendered
# in the terminal.
UI_WIDTH_CHARS = 44

# Horizontal and vertical padding for the UI layout.
PAD_X = " " * 2
PAD_Y = "\n"


class CliApp:
	"""Terminal-based interface for controlling audio playback
	with vamp loops.

	This class renders a live-updating status line showing
	playback progress, the current vamp state, and handles
	keyboard input for user interaction. It reads from a
	VampEngine instance which manages the actual audio playback
	and vamp logic.
	"""

	# How often (in seconds) the status line redraws. This
	# controls the visual smoothness of the progress bar and
	# timestamp updates.
	CLI_REFRESH_INTERVAL_SECONDS: float = 0.05

	def __init__(
			self, engine: VampEngine, filename: str = "",
	) -> None:
		"""Initialise the CLI application.

		Args:
			engine (VampEngine): The VampEngine instance that
				handles audio playback and vamp behaviour. The
				CLI reads state from this engine and sends user
				commands (exit vamp, quit) back to it.
			filename (str): The path to the audio file being
				played. Used only for display purposes in the
				header. If empty, the file label is omitted from
				the header.
		"""
		self._engine: VampEngine = engine
		self._filename: str = filename
		self._key_thread: threading.Thread | None = None

		# Number of terminal lines the previous render occupied.
		# Used to move the cursor back up before overwriting.
		self._rendered_lines: int = 0
		self._vamp_char_colours: dict[int, str] = (
			self._precompute_vamp_colours()
		)

	def run(self) -> None:
		"""Run the CLI application from start to finish.

		This is the main entry point. It prints the header,
		starts listening for keyboard input on a background
		thread, begins audio playback, and then blocks on the
		status loop until playback completes or the user quits.
		After the loop exits, it stops the engine and waits for
		the keyboard thread to shut down.
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
		"""Print the initial header block to the terminal.

		Displays the application title, audio file metadata
		(filename, duration, sample rate), and the available
		keyboard controls.
		"""
		title = f"{BOLD}{CYAN}AutoVamp{RESET} {DIM}(v{__version__}){RESET}"
		sep = f"{PAD_X}{'─' * UI_WIDTH_CHARS}"

		file_label = ""
		if self._filename:
			# Only show the base filename, not the full path, to
			# keep the header compact and readable.
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

		vamps = self._engine.vamps

		for i, vamp in enumerate(vamps, 1):
			colour = vamp.behaviour.colour
			name = str(vamp.behaviour)
			start = format_timestamp(vamp.start_time)
			end = format_timestamp(vamp.end_time)
			left = f"({i}) {name}"
			right = f"{start}–{end}"
			gap = UI_WIDTH_CHARS - len(left) - len(right)

			print(
				f"{PAD_X}{colour}{left}{RESET}"
				f"{' ' * gap}"
				f"{DIM}{right}{RESET}"
			)

		print(sep)
		print(
			f"{PAD_X}{DIM}SPACE:{RESET} exit vamp   "
			f"{DIM}Q:{RESET} quit"
		)
		print(sep)
		print(PAD_Y, end="")

	def _start_key_reader(self) -> None:
		"""Start a background thread that listens for keyboard input.

		Keyboard reading requires platform-specific code. On
		Windows, we use msvcrt to poll for keypresses. On Unix
		systems, we switch stdin to raw mode using termios so
		that individual characters are delivered immediately
		without waiting for the user to press Enter. The original
		terminal settings are restored when the thread exits.

		The background thread runs as a daemon so it will not
		prevent the process from exiting if something goes wrong.
		"""

		def handle_key(char: str) -> bool:
			"""Process a single keypress.

			Args:
				char (str): The character that was pressed.

			Returns:
				bool: True if the key reader should continue
					listening for input, False if the user
					requested to quit and the thread should stop.
			"""
			if char == " ":
				# Ask the engine to begin exiting the current
				# vamp. The engine delegates to the vamp's
				# behaviour, which may exit immediately or allow
				# a set number of remaining iterations first.
				self._engine.exit_current_vamp()
			elif char == "q":
				self._engine.stop()
				return False

			return True

		if sys.platform == "win32":
			def read_keys() -> None:
				import msvcrt
				while not self._engine.done.is_set():
					if msvcrt.kbhit():
						if not handle_key(msvcrt.getwch()):
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
					# Switch to raw mode so we receive each
					# keypress individually without requiring
					# the user to press Enter.
					tty.setraw(fd)

					while not self._engine.done.is_set():
						# Use select with a timeout so we can
						# periodically check whether the engine
						# has finished, rather than blocking
						# indefinitely on stdin.
						ready, _, _ = select.select(
							[sys.stdin],
							[],
							[],
							KEY_POLL_INTERVAL_SECONDS,
						)

						if ready:
							if not handle_key(sys.stdin.read(1)):
								return
				finally:
					# Always restore the original terminal
					# settings, even if an exception occurs, so
					# the user's shell is not left in raw mode.
					termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

		self._key_thread = threading.Thread(
			target=read_keys, daemon=True,
		)

		self._key_thread.start()

	def _precompute_vamp_colours(self) -> dict[int, str]:
		"""Map each progress bar character position to a vamp colour.

		Positions that fall outside any vamp region are not
		included in the dict; callers should default to CYAN.
		"""
		duration = self._engine.duration_seconds

		if duration <= 0:
			return {}

		pos_colours: dict[int, str] = {}

		for vamp in self._engine.vamps:
			colour = vamp.behaviour.colour
			vamp_start_frac = (vamp.start_time.total_seconds() / duration)
			vamp_end_frac = (vamp.end_time.total_seconds() / duration)
			char_start = int(vamp_start_frac * UI_WIDTH_CHARS)
			char_end = int(vamp_end_frac * UI_WIDTH_CHARS)

			for i in range(char_start, char_end + 1):
				if 0 <= i < UI_WIDTH_CHARS:
					pos_colours[i] = colour

		return pos_colours

	def _build_progress_bar(self, fraction: float, ) -> str:
		"""Build a text-based progress bar with ANSI colour codes.

		Each character position maps to a time range in the
		audio file. Characters within a vamp region are coloured
		using the precomputed colour map; others are cyan. The
		played portion uses filled block characters and the
		remaining portion uses dimmed shade characters.

		Args:
			fraction (float): A value between 0.0 and 1.0
				representing how far through the audio file
				playback has progressed.

		Returns:
			str: A string containing the rendered progress bar
				with ANSI colour codes, ready to be printed to
				the terminal.
		"""
		if self._engine.duration_seconds <= 0:
			return f"{DIM}{'░' * UI_WIDTH_CHARS}{RESET}"

		filled_pos = int(fraction * UI_WIDTH_CHARS)
		filled_pos = max(0, min(filled_pos, UI_WIDTH_CHARS))

		# Build the bar character by character, batching
		# runs of the same style to reduce escape codes.
		bar = ""
		prev_code = ""

		for i in range(UI_WIDTH_CHARS):
			is_filled = i < filled_pos
			colour = self._vamp_char_colours.get(i, CYAN)
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
		"""Continuously redraw the status line until playback ends.

		This loop runs on the main thread and polls the engine
		for its current state at a regular interval. Each
		iteration overwrites the same terminal line using a
		carriage return, creating a live-updating display that
		shows the progress bar, current timestamp, vamp status,
		and any pending exit message.
		"""
		while not self._engine.done.is_set():
			state = self._engine.state

			# Convert the current playhead position from samples
			# to a human-readable timestamp for display.
			position_time = timedelta(
				seconds=(state.position_samples / self._engine.samplerate_hz)
			)

			total_duration = timedelta(seconds=self._engine.duration_seconds)

			position_str = format_timestamp(position_time)
			total_str = format_timestamp(total_duration)

			# Calculate how far through the file we are as a
			# fraction, used to determine how much of the
			# progress bar to fill.
			duration_seconds = self._engine.duration_seconds

			if duration_seconds > 0:
				progress_fraction = (
					state.position_samples
					/ (self._engine.samplerate_hz * duration_seconds)
				)
			else:
				progress_fraction = 0.0

			progress_bar = self._build_progress_bar(progress_fraction)

			# Show the vamp region timestamps while we are
			# inside a vamp.
			vamp_indicator = ""
			vamp_colour = CYAN

			if state.is_vamping and state.current_vamp is not None:
				vamp_colour = state.current_vamp.behaviour.colour
				vamp_start_str = format_timestamp(state.current_vamp.start_time)
				vamp_end_str = format_timestamp(state.current_vamp.end_time)
				vamp_indicator = (
					f"  {vamp_colour}{BOLD}VAMPING{RESET}"
					f" {DIM}{vamp_start_str}"
					f"–{vamp_end_str}{RESET}"
				)

			# Read the live status message from the current
			# vamp's behaviour, if one is active.
			status_message = None
			if state.current_vamp is not None and state.is_vamping:
				status_message = state.current_vamp.behaviour.status_message

			time_display = (
				f"{BOLD}{position_str}{RESET}"
				f" {DIM}/{RESET} {total_str}"
			)

			# Build the list of lines to render. The progress
			# bar and timestamps are always shown. The status
			# message appears on a separate line below.
			lines = [
				f"{PAD_X}{progress_bar}  {time_display}"
				f"{vamp_indicator}",
			]
			if status_message is not None:
				second_line = f"{PAD_X}{vamp_colour}{status_message}{RESET}"
			else:
				second_line = ""

			lines.append("")
			lines.append(second_line)

			# Move the cursor back up to overwrite all lines
			# from the previous render, then print each line
			# clearing any leftover characters.
			output_lines = ""
			if self._rendered_lines > 1:
				output_lines += f"\033[{self._rendered_lines - 1}A"

			for i, line in enumerate(lines):
				if i > 0:
					output_lines += "\n"
				output_lines += f"\r{line}\033[K"

			self._rendered_lines = len(lines)
			print(output_lines, end="", flush=True)

			time.sleep(self.CLI_REFRESH_INTERVAL_SECONDS)
