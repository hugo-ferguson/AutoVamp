"""Interactive timeline widget drawn on a Dear PyGui drawlist.

Renders cue regions as coloured rectangles, a played-progress
overlay, and a playhead line. Supports click-to-seek and
hover tooltips showing cue info or timestamps.
"""

from __future__ import annotations

from datetime import timedelta

import dearpygui.dearpygui as dpg

from ..engine import VampEngine
from ..models import Cue, format_timestamp

TIMELINE_HEIGHT = 36
TIMELINE_BG = (35, 35, 40, 255)
TIMELINE_PLAYED = (255, 255, 255, 25)
PLAYHEAD_COLOUR = (255, 255, 255, 230)
CUE_FILL_ALPHA = 70
CUE_BORDER_ALPHA = 180
POINT_CUE_WIDTH = 3

# Map ANSI colour codes from CueBehaviour.colour to RGBA.
ANSI_TO_RGBA: dict[str, tuple[int, int, int, int]] = {
	"\033[35m": (200, 100, 200, 255),
	"\033[34m": (100, 150, 255, 255),
	"\033[33m": (230, 200, 50, 255),
	"\033[32m": (100, 200, 100, 255),
}

DEFAULT_COLOUR = (150, 220, 220, 255)


class Timeline:
	"""Custom-drawn timeline with cue visualisation.

	Owns the drawlist widget and tooltip. Call draw(),
	handle_click(), and update_tooltip() each frame.

	Args:
		parent: The Dear PyGui parent to place the drawlist in.
		initial_width: Starting pixel width of the drawlist.
	"""

	def __init__(self, parent: int, initial_width: int) -> None:
		self._engine: VampEngine | None = None

		self._group = dpg.add_group(parent=parent)
		self._drawlist = dpg.add_drawlist(
			width=initial_width, height=TIMELINE_HEIGHT,
			parent=self._group,
		)
		with dpg.tooltip(self._group):
			self._tooltip_text = dpg.add_text("")

	@property
	def cues(self) -> list[Cue]:
		"""The cue list from the current engine, or empty."""
		if self._engine is None:
			return []
		return self._engine.cues

	def set_engine(self, engine: VampEngine | None) -> None:
		"""Bind a new engine (or None to clear)."""
		self._engine = engine

	def draw(self) -> None:
		"""Redraw the timeline for the current frame."""
		vp_width = dpg.get_viewport_client_width()
		if vp_width > 20:
			dpg.configure_item(
				self._drawlist, width=vp_width - 20,
			)

		dpg.delete_item(self._drawlist, children_only=True)

		rect = dpg.get_item_rect_size(self._drawlist)
		width = rect[0] if rect else 0
		height = TIMELINE_HEIGHT

		if (
			self._engine is None
			or width <= 0
			or self._engine.duration_seconds <= 0
		):
			dpg.draw_rectangle(
				(0, 0), (width, height),
				fill=TIMELINE_BG,
				parent=self._drawlist,
			)
			return

		duration = self._engine.duration_seconds

		dpg.draw_rectangle(
			(0, 0), (width, height),
			fill=TIMELINE_BG,
			color=(0, 0, 0, 0),
			parent=self._drawlist,
		)

		for cue in self._engine.cues:
			colour = ANSI_TO_RGBA.get(
				cue.behaviour.colour, DEFAULT_COLOUR,
			)
			fill = (*colour[:3], CUE_FILL_ALPHA)
			border = (*colour[:3], CUE_BORDER_ALPHA)

			start_s = cue.start_time.total_seconds()
			x_start = (start_s / duration) * width

			if cue.end_time is not None:
				end_s = cue.end_time.total_seconds()
				x_end = (end_s / duration) * width
			else:
				x_end = x_start + POINT_CUE_WIDTH

			dpg.draw_rectangle(
				(x_start, 1), (x_end, height - 1),
				fill=fill,
				color=border,
				parent=self._drawlist,
			)

		state = self._engine.state
		fraction = state.position_samples / (
			self._engine.samplerate_hz * duration
		)
		played_x = fraction * width

		dpg.draw_rectangle(
			(0, 0), (played_x, height),
			fill=TIMELINE_PLAYED,
			color=(0, 0, 0, 0),
			parent=self._drawlist,
		)

		dpg.draw_line(
			(played_x, 0), (played_x, height),
			color=PLAYHEAD_COLOUR,
			thickness=2,
			parent=self._drawlist,
		)

	def handle_click(self) -> None:
		"""Seek the engine when the user clicks the timeline."""
		if self._engine is None:
			return
		if not dpg.is_mouse_button_clicked(dpg.mvMouseButton_Left):
			return

		duration = self._engine.duration_seconds
		if duration <= 0:
			return

		fraction = self._mouse_fraction()
		if fraction is None:
			return

		target = fraction * duration
		current = (
			self._engine.state.position_samples
			/ self._engine.samplerate_hz
		)
		self._engine.seek(target - current)

		if self._engine.state.is_paused:
			self._engine.toggle_pause()

	def update_tooltip(self) -> None:
		"""Update the tooltip to show cue info or timestamp."""
		if self._engine is None:
			return

		duration = self._engine.duration_seconds
		if duration <= 0:
			return

		fraction = self._mouse_fraction()
		if fraction is None:
			return

		time_at_mouse = fraction * duration

		tw = self._timeline_width()
		for cue in self._engine.cues:
			cue_start = cue.start_time.total_seconds()
			if cue.end_time is not None:
				cue_end = cue.end_time.total_seconds()
			elif tw > 0:
				# Give a point cue the same hover width it's drawn with.
				cue_end = cue_start + (duration * POINT_CUE_WIDTH / tw)
			else:
				cue_end = cue_start

			if cue_start <= time_at_mouse <= cue_end:
				name = str(cue.behaviour)
				start_str = format_timestamp(cue.start_time)
				if cue.end_time is not None:
					end_str = format_timestamp(cue.end_time)
					text = f"{name}: {start_str} - {end_str}"
				else:
					text = f"{name}: {start_str}"
				dpg.set_value(self._tooltip_text, text)
				return

		ts = format_timestamp(timedelta(seconds=time_at_mouse))
		dpg.set_value(self._tooltip_text, ts)

	def _mouse_fraction(self) -> float | None:
		"""Return the mouse position as a 0-1 fraction of the
		timeline width, or None if the mouse is outside."""
		mx, my = dpg.get_mouse_pos(local=False)
		tmin = dpg.get_item_rect_min(self._drawlist)
		tmax = dpg.get_item_rect_max(self._drawlist)

		if not (
			tmin[0] <= mx <= tmax[0]
			and tmin[1] <= my <= tmax[1]
		):
			return None

		tw = tmax[0] - tmin[0]
		if tw <= 0:
			return None

		return (mx - tmin[0]) / tw

	def _timeline_width(self) -> float:
		"""Return the current rendered width of the drawlist."""
		tmin = dpg.get_item_rect_min(self._drawlist)
		tmax = dpg.get_item_rect_max(self._drawlist)
		return tmax[0] - tmin[0]
