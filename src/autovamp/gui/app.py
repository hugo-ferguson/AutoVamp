"""Graphical frontend for AutoVamp using Dear PyGui.

Mirrors CliApp's relationship with VampEngine: polls
engine.state each frame, calls engine control methods in
response to user input. Supports multi-track playback,
keyboard shortcuts, and a file picker for standalone launch.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import timedelta

import dearpygui.dearpygui as dpg

from .. import __version__
from ..engine import VampEngine
from ..models import PlaybackState, Track, format_timestamp
from . import dialogs
from .timeline import ANSI_TO_RGBA, DEFAULT_COLOUR, Timeline

WINDOW_WIDTH = 700
WINDOW_HEIGHT = 800
FONT_SIZE = 18

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_PATH = os.path.join(_FONT_DIR, "OpenSans-Regular.ttf")
FONT_BOLD_PATH = os.path.join(_FONT_DIR, "OpenSans-Bold.ttf")


class GuiApp:
	"""Graphical frontend for AutoVamp.

	Plays one or more tracks in sequence. Between tracks,
	the app waits for the user to press Play (unless the
	next track has autostart enabled).

	Args:
		tracks: List of tracks to play in order. May be
			empty if the app should show a file picker.
	"""

	def __init__(self, tracks: list[Track]) -> None:
		self._tracks: list[Track] = tracks
		self._engine: VampEngine | None = None
		self._track_index: int = 0
		# True between tracks, when we hold for the user to press Play.
		self._waiting: bool = False
		# Set once every track has finished, so the end-of-playback
		# handler runs only once rather than on every frame.
		self._finished: bool = False
		self._prev_state: PlaybackState | None = None
		self._bold_font: int = 0
		self._timeline: Timeline | None = None

		# Dear PyGui widget tags, populated in _build_ui. Kept on the
		# instance so _update can mutate them each frame.
		self._track_info_text: int = 0
		self._duration_text: int = 0
		self._time_text: int = 0
		self._status_text: int = 0
		self._substatus_text: int = 0
		self._cue_group: int = 0
		self._cue_texts: list[int] = []
		self._play_button: int = 0
		self._exit_cue_button: int = 0
		self._restart_button: int = 0
		self._track_nav_group: int = 0
		self._track_list_group: int = 0
		self._track_texts: list[int] = []

	def run(self) -> None:
		"""Create the Dear PyGui context and run the app."""
		dpg.create_context()
		dpg.create_viewport(
			title=f"AutoVamp v{__version__}",
			width=WINDOW_WIDTH,
			height=WINDOW_HEIGHT,
			resizable=True,
		)

		self._load_font()
		self._build_ui()
		self._setup_key_handlers()

		dpg.setup_dearpygui()
		dpg.show_viewport()

		if self._tracks:
			self._load_track(0)
		else:
			dialogs.show_file_picker(self._on_file_selected)

		while dpg.is_dearpygui_running():
			self._update()
			dpg.render_dearpygui_frame()
			playing = (
				self._prev_state is not None
				and self._prev_state.is_playing
				and not self._prev_state.is_paused
			)
			time.sleep(1 / 30 if playing else 1 / 10)

		if self._engine is not None:
			self._engine.stop()

		dpg.destroy_context()

	def _load_font(self) -> None:
		"""Load the bundled Open Sans regular and bold fonts."""
		if not os.path.isfile(FONT_PATH):
			# Usually means a frozen build didn't bundle the fonts
			# directory; fall back to Dear PyGui's default font.
			print(
				f"Warning: font not found at {FONT_PATH}, "
				f"using the default font.",
				file=sys.stderr,
			)
			return
		with dpg.font_registry():
			font = dpg.add_font(FONT_PATH, FONT_SIZE)
			if os.path.isfile(FONT_BOLD_PATH):
				self._bold_font = dpg.add_font(
					FONT_BOLD_PATH, FONT_SIZE,
				)
		dpg.bind_font(font)

	def _build_ui(self) -> None:
		"""Create all widgets in the main window."""
		with dpg.window(tag="main_window"):
			dpg.set_primary_window("main_window", True)

			with dpg.menu_bar():
				with dpg.menu(label="File"):
					dpg.add_menu_item(
						label="Open...",
						callback=lambda: dialogs.show_file_picker(
							self._on_file_selected,
						),
					)
				with dpg.menu(label="Help"):
					dpg.add_menu_item(
						label="Help",
						callback=lambda: dialogs.show_help(),
					)
					dpg.add_menu_item(
						label="About",
						callback=lambda: dialogs.show_about(),
					)

			dpg.add_text(f"AutoVamp v{__version__}")
			dpg.add_spacer(height=4)
			dpg.add_separator()
			dpg.add_spacer(height=4)

			self._track_list_group = dpg.add_group(show=False)
			dpg.add_separator()
			dpg.add_spacer(height=4)

			self._track_info_text = dpg.add_text(
				"No file loaded",
			)
			self._duration_text = dpg.add_text("")
			dpg.add_spacer(height=4)
			dpg.add_separator()
			dpg.add_spacer(height=6)

			self._timeline = Timeline(
				parent=dpg.last_container(),
				initial_width=WINDOW_WIDTH,
			)

			dpg.add_spacer(height=2)
			self._time_text = dpg.add_text(
				"00:00:00.000 / 00:00:00.000",
			)
			dpg.add_spacer(height=4)
			dpg.add_separator()
			dpg.add_spacer(height=4)

			self._status_text = dpg.add_text("STOPPED")
			if self._bold_font:
				dpg.bind_item_font(
					self._status_text, self._bold_font,
				)
			self._substatus_text = dpg.add_text("")
			dpg.add_spacer(height=4)
			dpg.add_separator()
			dpg.add_spacer(height=4)

			dpg.add_text("Cues:")
			self._cue_group = dpg.add_group()
			dpg.add_spacer(height=4)
			dpg.add_separator()
			dpg.add_spacer(height=6)

			# Bound methods are wrapped in lambdas throughout: in compiled
			# builds (Nuitka) a bound method is a 'compiled_method' that
			# Dear PyGui's dispatcher fails to recognise as a method, so it
			# miscounts the arguments and silently drops the callback. A
			# lambda is a plain function that DPG introspects correctly.
			with dpg.group(horizontal=True):
				self._play_button = dpg.add_button(
					label="Play [Space]",
					callback=lambda: self._on_play_pause(),
				)
				self._exit_cue_button = dpg.add_button(
					label="Exit Cue [Enter]",
					callback=lambda: self._on_exit_cue(),
					show=False,
				)
				self._restart_button = dpg.add_button(
					label="Restart [Esc]",
					callback=lambda: self._on_restart(),
				)

			dpg.add_spacer(height=4)
			with dpg.group(horizontal=True):
				dpg.add_button(
					label="-30s",
					callback=lambda: self._on_seek(-30),
				)
				dpg.add_button(
					label="-5s",
					callback=lambda: self._on_seek(-5),
				)
				dpg.add_button(
					label="-1s",
					callback=lambda: self._on_seek(-1),
				)
				dpg.add_button(
					label="+1s",
					callback=lambda: self._on_seek(1),
				)
				dpg.add_button(
					label="+5s",
					callback=lambda: self._on_seek(5),
				)
				dpg.add_button(
					label="+30s",
					callback=lambda: self._on_seek(30),
				)

			dpg.add_spacer(height=4)

			self._track_nav_group = dpg.add_group(
				horizontal=True, show=False,
			)
			dpg.add_button(
				label="Prev Track [Up]",
				callback=lambda: self._prev_track(),
				parent=self._track_nav_group,
			)
			dpg.add_button(
				label="Next Track [Down]",
				callback=lambda: self._next_track(),
				parent=self._track_nav_group,
			)

			dpg.add_spacer(height=4)
			dpg.add_separator()
			dpg.add_spacer(height=4)
			with dpg.collapsing_header(
				label="Keyboard Shortcuts",
				default_open=False,
			):
				dpg.add_text(
					"Space        Play / Pause\n"
					"Enter        Exit current cue\n"
					"Escape       Restart track\n"
					"Left/Right   Seek 5s\n"
					"Alt+L/R      Seek 1s\n"
					"Ctrl+L/R     Seek 30s\n"
					"Up/Down      Prev / Next track",
				)

	def _setup_key_handlers(self) -> None:
		"""Register keyboard shortcuts."""
		with dpg.handler_registry():
			dpg.add_key_press_handler(
				dpg.mvKey_Spacebar,
				callback=lambda: self._on_play_pause(),
			)
			dpg.add_key_press_handler(
				dpg.mvKey_Return,
				callback=lambda: self._on_exit_cue(),
			)
			dpg.add_key_press_handler(
				dpg.mvKey_Escape,
				callback=lambda: self._on_restart(),
			)
			dpg.add_key_press_handler(
				dpg.mvKey_Left,
				callback=lambda: self._on_left_arrow(),
			)
			dpg.add_key_press_handler(
				dpg.mvKey_Right,
				callback=lambda: self._on_right_arrow(),
			)
			dpg.add_key_press_handler(
				dpg.mvKey_Up,
				callback=lambda: self._prev_track(),
			)
			dpg.add_key_press_handler(
				dpg.mvKey_Down,
				callback=lambda: self._next_track(),
			)

	def _build_cue_list(self) -> None:
		"""Populate the cue list group for the current track."""
		for tag in self._cue_texts:
			dpg.delete_item(tag)
		self._cue_texts.clear()

		if self._engine is None:
			return

		for i, cue in enumerate(self._engine.cues, 1):
			colour = ANSI_TO_RGBA.get(
				cue.behaviour.colour, DEFAULT_COLOUR,
			)
			name = str(cue.behaviour)
			start = format_timestamp(cue.start_time)

			if cue.end_time is not None:
				end = format_timestamp(cue.end_time)
				times = f"{start} - {end}"
			else:
				times = start

			row = dpg.add_group(
				horizontal=True, parent=self._cue_group,
			)
			name_tag = dpg.add_text(
				f"  ({i}) {name}", parent=row, color=colour,
			)
			if self._bold_font:
				dpg.bind_item_font(name_tag, self._bold_font)
			dpg.add_text(times, parent=row, color=colour)

			self._cue_texts.append(row)

	def _build_track_list(self) -> None:
		"""Populate the track list for multi-track configs."""
		for tag in self._track_texts:
			dpg.delete_item(tag)
		self._track_texts.clear()

		show = len(self._tracks) > 1
		dpg.configure_item(self._track_list_group, show=show)
		dpg.configure_item(self._track_nav_group, show=show)

		if not show:
			return

		for i, track in enumerate(self._tracks):
			basename = os.path.basename(track.filepath)
			tag = dpg.add_text(
				f"  ({i + 1}) {basename}",
				parent=self._track_list_group,
			)
			self._track_texts.append(tag)

	def _load_track(self, index: int) -> None:
		"""Load a track by index and prepare for playback.

		Args:
			index: Position of the track in self._tracks.
		"""
		if self._engine is not None:
			self._engine.stop()

		self._track_index = index
		self._finished = False
		self._prev_state = None
		track = self._tracks[index]

		try:
			self._engine = VampEngine(
				filepath=track.filepath, cues=track.cues,
			)
		except Exception as e:
			self._engine = None
			dialogs.show_error(f"Cannot load track: {e}")
			return

		self._timeline.set_engine(self._engine)

		basename = os.path.basename(track.filepath)
		dpg.set_value(
			self._track_info_text, f"Track: {basename}",
		)

		duration = self._engine.duration_seconds
		rate = self._engine.samplerate_hz
		dpg.set_value(
			self._duration_text,
			f"Duration: {duration:.1f}s   "
			f"Sample rate: {rate}Hz",
		)

		self._build_cue_list()
		self._build_track_list()
		self._update_track_list()

		dpg.set_value(self._status_text, "STOPPED")
		dpg.set_value(self._substatus_text, "")
		dpg.configure_item(self._play_button, label="Play")

		if track.autostart:
			self._engine.play()
			self._waiting = False
		else:
			self._waiting = True

	def _update_track_list(self) -> None:
		"""Update track list markers to show current track."""
		for i, tag in enumerate(self._track_texts):
			basename = os.path.basename(
				self._tracks[i].filepath,
			)
			if i < self._track_index:
				dpg.set_value(
					tag, f"  ({i + 1}) [done] {basename}",
				)
				dpg.bind_item_font(tag, 0)
			elif i == self._track_index:
				if self._bold_font:
					dpg.bind_item_font(tag, self._bold_font)
			else:
				dpg.set_value(
					tag, f"  ({i + 1})   {basename}",
				)
				dpg.bind_item_font(tag, 0)

	def _next_track(self) -> None:
		"""Skip to the next track."""
		if self._track_index < len(self._tracks) - 1:
			self._load_track(self._track_index + 1)

	def _prev_track(self) -> None:
		"""Skip to the previous track."""
		if self._track_index > 0:
			self._load_track(self._track_index - 1)

	def _on_track_ended(self) -> None:
		"""Handle playback reaching the end of a track."""
		if self._engine is not None:
			self._engine.stop()

		next_index = self._track_index + 1
		if next_index >= len(self._tracks):
			self._finished = True
			dpg.set_value(self._status_text, "DONE")
			dpg.set_value(self._substatus_text, "")
			dpg.configure_item(self._play_button, label="Play")
			return

		self._load_track(next_index)

	def _update(self) -> None:
		"""Update widgets when engine state changes."""
		if self._engine is None:
			return

		state = self._engine.state

		if (
			self._engine.done.is_set()
			and not state.is_playing
			and not self._waiting
			and not self._finished
		):
			self._on_track_ended()
			return

		if state == self._prev_state:
			return
		self._prev_state = state

		self._timeline.draw()
		self._timeline.update_tooltip()

		duration = self._engine.duration_seconds
		position = timedelta(
			seconds=(
				state.position_samples
				/ self._engine.samplerate_hz
			),
		)
		total = timedelta(seconds=duration)
		dpg.set_value(
			self._time_text,
			f"{format_timestamp(position)} / "
			f"{format_timestamp(total)}",
		)

		if self._waiting:
			dpg.set_value(
				self._status_text, "Press Play to start",
			)
			dpg.set_value(self._substatus_text, "")
			dpg.configure_item(
				self._play_button, label="Play",
			)
		elif state.in_cue and state.current_cue is not None:
			msg = state.current_cue.behaviour.active_message
			dpg.set_value(self._status_text, msg)
			sub = state.current_cue.behaviour.status_message
			dpg.set_value(
				self._substatus_text, sub if sub else "",
			)
		elif state.is_paused:
			dpg.set_value(self._status_text, "PAUSED")
			dpg.set_value(self._substatus_text, "")
		elif state.is_playing:
			dpg.set_value(self._status_text, "PLAYING")
			dpg.set_value(self._substatus_text, "")
		else:
			dpg.set_value(self._status_text, "STOPPED")
			dpg.set_value(self._substatus_text, "")

		if not self._waiting:
			if state.is_paused or not state.is_playing:
				dpg.configure_item(
					self._play_button, label="Play [Space]",
				)
			else:
				dpg.configure_item(
					self._play_button, label="Pause [Space]",
				)

		if state.in_cue and state.current_cue is not None:
			label = state.current_cue.behaviour.exit_label
			dpg.configure_item(
				self._exit_cue_button,
				label=f"{label} [Enter]",
				show=True,
			)
		else:
			dpg.configure_item(
				self._exit_cue_button, show=False,
			)

		self._highlight_active_cue(state)
		self._update_current_track_label(state)

	def _highlight_active_cue(self, state: PlaybackState) -> None:
		"""Brighten the cue list row for the cue currently playing.

		Args:
			state: The latest engine state snapshot.
		"""
		assert self._engine is not None
		for i, row in enumerate(self._cue_texts):
			cue = self._engine.cues[i]
			colour = ANSI_TO_RGBA.get(
				cue.behaviour.colour, DEFAULT_COLOUR,
			)
			# Lift the active cue above the others so it stands out.
			if (
				state.in_cue
				and state.current_cue is not None
				and state.current_cue is cue
			):
				bright = tuple(
					min(255, c + 80) for c in colour[:3]
				) + (255,)
				for child in dpg.get_item_children(row, 1):
					dpg.configure_item(child, color=bright)
			else:
				for child in dpg.get_item_children(row, 1):
					dpg.configure_item(child, color=colour)

	def _update_current_track_label(self, state: PlaybackState) -> None:
		"""Tag the current track in the list with its play state.

		Args:
			state: The latest engine state snapshot.
		"""
		if not (
			self._track_texts
			and self._track_index < len(self._track_texts)
		):
			return

		tag = self._track_texts[self._track_index]
		basename = os.path.basename(
			self._tracks[self._track_index].filepath,
		)
		idx = self._track_index + 1
		if state.is_paused or self._waiting:
			label = "Paused"
		elif state.is_playing:
			label = "Playing"
		else:
			label = "Stopped"
		dpg.set_value(
			tag, f"  ({idx}) [{label}] {basename}",
		)

	def _on_play_pause(self) -> None:
		"""Toggle play/pause or start playback if waiting."""
		if self._engine is None:
			return

		if self._waiting:
			self._engine.play()
			self._waiting = False
			return

		if self._engine.state.paused_by_cue:
			self._engine.exit_current_cue()
			return

		if not self._engine.state.is_playing:
			self._engine.play()
		else:
			self._engine.toggle_pause()

	def _on_exit_cue(self) -> None:
		"""Request exit from the current cue."""
		if self._engine is not None:
			self._engine.exit_current_cue()

	def _on_restart(self) -> None:
		"""Restart the current track from the beginning."""
		if self._engine is not None:
			self._engine.seek(-1e9)

	def _on_seek(self, offset: float) -> None:
		"""Seek by a relative offset in seconds.

		Args:
			offset: Seconds to move (negative seeks backwards).
		"""
		if self._engine is not None:
			self._engine.seek(offset)

	def _on_left_arrow(self) -> None:
		"""Handle left arrow with modifier detection."""
		if dpg.is_key_down(dpg.mvKey_Control):
			self._on_seek(-30)
		elif dpg.is_key_down(dpg.mvKey_Alt):
			self._on_seek(-1)
		else:
			self._on_seek(-5)

	def _on_right_arrow(self) -> None:
		"""Handle right arrow with modifier detection."""
		if dpg.is_key_down(dpg.mvKey_Control):
			self._on_seek(30)
		elif dpg.is_key_down(dpg.mvKey_Alt):
			self._on_seek(1)
		else:
			self._on_seek(5)

	def _on_file_selected(
		self, sender: int, app_data: dict,
	) -> None:
		"""Handle file selection from the picker.

		Args:
			sender: The Dear PyGui file dialog item (unused).
			app_data: Dialog result; its 'file_path_name' key
				holds the chosen path.
		"""
		filepath = app_data.get("file_path_name", "")
		if not filepath:
			return

		try:
			self._tracks = dialogs.load_file(filepath)
			self._load_track(0)
		except SystemExit as e:
			dialogs.show_error(str(e))
		except Exception as e:
			dialogs.show_error(str(e))
