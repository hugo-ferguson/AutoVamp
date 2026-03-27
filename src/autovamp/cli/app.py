"""Terminal-based user interface for AutoVamp.

Renders a live-updating progress bar and status display and
handles keyboard input for controlling playback. Supports
multiple tracks played in sequence.
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
from ..models import Track, format_timestamp

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

# Unicode symbols used in the UI.
SYM_PLAY = "\u25b6"       		# ▶
SYM_PAUSE = "\u2759\u2759"  	# ❙❙
SYM_CHECK = "\u2713"      		# ✓
SYM_DASH = "\u2013"       		# –
SYM_DOT = "\u00b7"        		# ·
SYM_PLUS_MINUS = "\u00b1"  		# ±
SYM_ARROW_L = "\u2190"  		# ←
SYM_ARROW_R = "\u2192"  		# →

# Progress bar width in characters.
BAR_WIDTH = 40

# Horizontal and vertical padding for the UI layout.
PAD_X = "  "
PAD_Y = "\n"

# Width of the play state indicator prefix on the status line.
STATE_PREFIX_WIDTH = 2

# Key bindings: (raw_bytes, label, description, action).
# Entries with an empty label are hidden in the header but
# still dispatched. Action is a name or "seek:<seconds>".
KEY_BINDINGS: list[tuple[tuple[bytes, ...], str, str, str]] = [
	((b" ",), "SPACE", "play/pause", "play_pause"),
	((b"\r", b"\n"), "ENTER", "exit cue", "exit_cue"),
	((b"q", b"Q"), "Q", "quit", "quit"),
	((b"\x1b[D",), f"{SYM_ARROW_L}/{SYM_ARROW_R}", f"{SYM_PLUS_MINUS}5s", "seek:-5"),
	((b"\x1b[C",), "", "", "seek:5"),
	((b"\x1b[1;3D",), f"ALT+{f"{SYM_ARROW_L}/{SYM_ARROW_R}"}", f"{SYM_PLUS_MINUS}1s", "seek:-1"),
	((b"\x1b[1;3C",), "", "", "seek:1"),
	((b"\x1b[1;5D",), f"CTRL+{f"{SYM_ARROW_L}/{SYM_ARROW_R}"}", f"{SYM_PLUS_MINUS}30s", "seek:-30"),
	((b"\x1b[1;5C",), "", "", "seek:30"),
	((b"\x1b",), "ESC", "restart", "restart"),
]

# Total display width: PAD_X + state prefix + bar + decorations.
# Used for separator lines and right-aligned text.
UI_WIDTH = STATE_PREFIX_WIDTH + BAR_WIDTH + 1


class CliApp:
	"""Terminal interface for audio playback with cues.

	Plays one or more tracks in sequence. Between tracks, the
	app pauses and waits for user input (unless the next track
	has autostart enabled). A single key reader thread persists
	across all tracks.

	Args:
		tracks: List of tracks to play in order.
	"""

	def __init__(self, tracks: list[Track]) -> None:
		self._tracks: list[Track] = tracks
		self._engine: VampEngine | None = None
		self._key_thread: threading.Thread | None = None
		self._rendered_lines: int = 0
		self._cue_char_colours: dict[int, str] = {}

		# Set when the user quits or all tracks are done.
		self._app_done: threading.Event = threading.Event()

		# Set by the key reader to advance past a between-track
		# pause. Cleared before each wait.
		self._next_track: threading.Event = threading.Event()

		# True while the app is paused between tracks, so the
		# key reader knows to route SPACE to _next_track.
		self._waiting: bool = False

	def run(self) -> None:
		"""Play all tracks in sequence.

		Prints the app header once, starts the key reader, then
		iterates through each track: creates an engine, prints
		track info, plays until done, and optionally waits for
		user input before continuing.
		"""
		self._start_key_reader()

		for i, track in enumerate(self._tracks):
			if self._app_done.is_set():
				break

			engine = VampEngine(
				filepath=track.filepath, cues=track.cues,
			)
			self._engine = engine
			self._cue_char_colours = self._precompute_cue_colours()
			self._rendered_lines = 0

			self._validate_cues(track, engine)
			self._print_header(i + 1)

			engine.play()
			self._status_loop()
			engine.stop()

			if self._app_done.is_set():
				break

			# Wait between tracks unless the next one autostarts
			# or this is the last track.
			if i < len(self._tracks) - 1:
				next_track = self._tracks[i + 1]
				if not next_track.autostart:
					self._wait_for_next_track(i + 2)
					if self._app_done.is_set():
						break

		self._app_done.set()

		if self._key_thread is not None:
			self._key_thread.join(timeout=1.0)

		print(f"{PAD_Y}{PAD_X}{GREEN}{BOLD}Done.{RESET}{PAD_Y}")

	def _validate_cues(self, track: Track, engine: VampEngine) -> None:
		"""Check that no cue extends past the end of the track.

		Args:
			track: The track whose cues to validate.
			engine: The engine loaded with the track's audio.
		"""
		duration = engine.duration_seconds
		for cue in track.cues:
			check_time = cue.end_time or cue.start_time
			if check_time.total_seconds() > duration:
				ts = format_timestamp(check_time)
				dur = format_timestamp(
					timedelta(seconds=duration),
				)
				basename = os.path.basename(track.filepath)
				print(
					f"Error: cue at {ts} exceeds duration "
					f"of {basename} ({dur})"
				)
				raise SystemExit(1)

	# ── Header ─────────────────────────────────────────────

	def _print_header(self, track_num: int) -> None:
		"""Print the full header block for a track.

		Includes the app title, track list (for multi-track),
		track metadata, cue list, and keyboard controls.

		Args:
			track_num: 1-based index of the current track.
		"""
		sep = f"{PAD_X}{'─' * UI_WIDTH}"
		engine = self._engine
		assert engine is not None
		total = len(self._tracks)

		title = (
			f"{BOLD}{CYAN}AutoVamp{RESET}"
			f" {DIM}(v{__version__}){RESET}"
		)

		print(PAD_Y, end="")
		print(f"{PAD_X}{title}")
		print(sep)

		# Track list (multi-track) or file label (single).
		if total > 1:
			for i, track in enumerate(self._tracks, 1):
				basename = os.path.basename(track.filepath)
				if i < track_num:
					marker = f"{DIM}{SYM_CHECK}{RESET}"
					label = f"{DIM}{basename}{RESET}"
				elif i == track_num:
					marker = f"{GREEN}{SYM_PLAY}{RESET}"
					label = basename
				else:
					marker = " "
					label = f"{DIM}{basename}{RESET}"
				print(f"{PAD_X}{marker} ({i}) {label}")
			print(sep)

		else:
			basename = os.path.basename(
				self._tracks[0].filepath,
			)
			print(f"{PAD_X}{DIM}File:{RESET} {basename}")

		# Duration and sample rate on one line.
		duration = engine.duration_seconds
		rate = engine.samplerate_hz
		print(
			f"{PAD_X}{DIM}Duration:{RESET} {duration:.1f}s"
			f"  {DIM}Sample rate:{RESET} {rate}Hz"
		)

		# Cue list.
		if engine.cues:
			print(sep)
			for i, cue in enumerate(engine.cues, 1):
				colour = cue.behaviour.colour
				name = str(cue.behaviour)
				start = format_timestamp(cue.start_time)
				left = f"({i}) {name}"

				if cue.end_time is not None:
					end = format_timestamp(cue.end_time)
					right = f"{start}{SYM_DASH}{end}"
				else:
					right = start

				gap = UI_WIDTH - len(left) - len(right)
				print(
					f"{PAD_X}{colour}{left}{RESET}"
					f"{' ' * gap}"
					f"{DIM}{right}{RESET}"
				)

		# Compact controls.
		print(sep)
		print(
			f"{PAD_X}{DIM}SPACE{RESET} play/pause"
			f" {DIM}{SYM_DOT}{RESET} "
			f"{DIM}ENTER{RESET} exit cue"
			f" {DIM}{SYM_DOT}{RESET} "
			f"{DIM}Q{RESET} quit"
		)
		print(
			f"{PAD_X}{DIM}{f"{SYM_ARROW_L}/{SYM_ARROW_R}"}{RESET} {SYM_PLUS_MINUS}5s"
			f" {DIM}{SYM_DOT}{RESET} "
			f"{DIM}ALT{RESET} {SYM_PLUS_MINUS}1s"
			f" {DIM}{SYM_DOT}{RESET} "
			f"{DIM}CTRL{RESET} {SYM_PLUS_MINUS}30s"
			f" {DIM}{SYM_DOT}{RESET} "
			f"{DIM}ESC{RESET} restart"
		)
		print(sep)
		print(PAD_Y, end="")

	# ── Key reader ─────────────────────────────────────────

	def _start_key_reader(self) -> None:
		"""Start a daemon thread that listens for keyboard input.

		The thread persists for the lifetime of the app. On
		Windows, uses msvcrt polling. On Unix, switches stdin
		to raw mode via termios and restores it on exit.
		"""

		# Build a dispatch map: raw bytes -> action string.
		dispatch: dict[bytes, str] = {}
		for raw_seqs, _, _, action in KEY_BINDINGS:
			for raw in raw_seqs:
				dispatch[raw] = action

		def handle_input(data: bytes) -> bool:
			"""Process raw input bytes from the terminal.

			Routes input differently depending on whether the
			app is waiting between tracks or playing.

			Args:
				data: Raw bytes received from stdin.

			Returns:
				True to keep listening, False to quit.
			"""
			action = dispatch.get(data)
			if action is None:
				return True

			# Between tracks, only SPACE and Q are active.
			if self._waiting:
				if action == "play_pause":
					self._next_track.set()
				elif action == "quit":
					self._app_done.set()
					return False
				return True

			if action == "play_pause":
				if self._engine is not None:
					self._engine.toggle_pause()
			elif action == "exit_cue":
				if self._engine is not None:
					self._engine.exit_current_cue()
			elif action == "quit":
				self._app_done.set()
				return False
			elif action == "restart":
				if self._engine is not None:
					self._engine.seek(-1e9)
			elif action.startswith("seek:"):
				if self._engine is not None:
					self._engine.seek(float(action.split(":")[1]))

			return True

		if sys.platform == "win32":
			def read_keys() -> None:
				import msvcrt
				while not self._app_done.is_set():
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
					tty.setcbreak(fd)

					while not self._app_done.is_set():
						# Poll with a timeout so we can check
						# if the app has finished.
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

	# ── Between-track waiting ──────────────────────────────

	def _wait_for_next_track(self, next_num: int) -> None:
		"""Pause between tracks until the user presses SPACE.

		Args:
			next_num: 1-based index of the upcoming track.
		"""
		self._waiting = True
		self._next_track.clear()

		basename = os.path.basename(
			self._tracks[next_num - 1].filepath,
		)

		print(
			f"{PAD_Y}{PAD_X}{DIM}"
			f"Up next: {basename}. "
			f"Press SPACE to continue.{RESET}"
		)

		while (
			not self._next_track.is_set()
			and not self._app_done.is_set()
		):
			time.sleep(KEY_POLL_INTERVAL_SECONDS)

		self._waiting = False

	# ── Progress bar ───────────────────────────────────────

	def _precompute_cue_colours(self) -> dict[int, str]:
		"""Map each progress bar character position to a colour.

		Positions outside any cue region are not included;
		callers should default to CYAN.

		Returns:
			A dict mapping character index to ANSI colour code.
		"""
		assert self._engine is not None
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

			char_start = int(cue_start_frac * BAR_WIDTH)
			char_end = max(char_start, int(cue_end_frac * BAR_WIDTH))

			for i in range(char_start, char_end + 1):
				if 0 <= i < BAR_WIDTH:
					pos_colours[i] = colour

		return pos_colours

	def _build_progress_bar(self, fraction: float) -> str:
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
		assert self._engine is not None

		if self._engine.duration_seconds <= 0:
			return f"{DIM}{'░' * BAR_WIDTH}{RESET}"

		filled_pos = int(fraction * BAR_WIDTH)
		filled_pos = max(0, min(filled_pos, BAR_WIDTH))

		# Build character by character, batching runs of the
		# same style to reduce escape codes.
		bar = ""
		prev_code = ""

		for i in range(BAR_WIDTH):
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

	# ── Status loop ────────────────────────────────────────

	def _status_loop(self) -> None:
		"""Redraw the status line until the current track ends.

		Polls the engine state at a regular interval and
		overwrites the same terminal lines each frame.
		"""
		assert self._engine is not None
		engine = self._engine

		while not engine.done.is_set() and not self._app_done.is_set():
			state = engine.state

			# Convert playhead position to a timestamp.
			position_time = timedelta(
				seconds=(state.position_samples / engine.samplerate_hz)
			)

			total_duration = timedelta(seconds=engine.duration_seconds)

			position_str = format_timestamp(position_time)
			total_str = format_timestamp(total_duration)

			# Progress fraction for the bar fill level.
			duration_seconds = engine.duration_seconds

			if duration_seconds > 0:
				progress_fraction = (
						state.position_samples
						/ (engine.samplerate_hz * duration_seconds)
				)
			else:
				progress_fraction = 0.0

			progress_bar = self._build_progress_bar(progress_fraction)

			# Play state indicator.
			if state.is_paused:
				state_icon = f"{DIM}{SYM_PAUSE}{RESET}"
			else:
				state_icon = f"{GREEN}{SYM_PLAY}{RESET} "

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
						f"{SYM_DASH}{cue_end_str}"
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
				f"{PAD_X}{state_icon} {progress_bar}"
				f"  {time_display}"
				f"{cue_indicator}",
			]

			if status_message is not None:
				second_line = (
					f"{PAD_X}{' ' * STATE_PREFIX_WIDTH}"
					f"{cue_colour}{status_message}{RESET}"
				)
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
