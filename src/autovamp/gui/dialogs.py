"""Dialog windows for the AutoVamp GUI.

Provides the About, Help, Error, and File Picker dialogs.
All dialogs are rendered inside the Dear PyGui viewport.
"""

from __future__ import annotations

import os

import dearpygui.dearpygui as dpg

from .. import __version__
from ..__main__ import load_toml
from ..models import Track

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
HELP_PATH = os.path.join(_GUI_DIR, "help.txt")

AUDIO_COLOUR = (150, 220, 220, 255)
TOML_COLOUR = (100, 200, 100, 255)


def show_file_picker(callback) -> None:
	"""Show a file dialog for selecting audio or TOML files.

	Args:
		callback: Dear PyGui callback receiving (sender, app_data).
	"""
	with dpg.file_dialog(
		label="Open Audio or TOML File",
		callback=callback,
		width=580,
		height=500,
	):
		dpg.add_file_extension(".toml", color=TOML_COLOUR)
		dpg.add_file_extension(".wav", color=AUDIO_COLOUR)
		dpg.add_file_extension(".flac", color=AUDIO_COLOUR)
		dpg.add_file_extension(".ogg", color=AUDIO_COLOUR)
		dpg.add_file_extension(".mp3", color=AUDIO_COLOUR)


def load_file(filepath: str) -> list[Track]:
	"""Load tracks from a filepath (TOML or raw audio).

	Args:
		filepath: Path to a .toml config or audio file.

	Returns:
		A list of Track instances.
	"""
	if filepath.endswith(".toml"):
		return load_toml(filepath)
	return [Track(filepath=filepath, cues=[])]


def show_about() -> None:
	"""Show the About dialog."""
	tag = "about_dialog"
	if dpg.does_item_exist(tag):
		dpg.show_item(tag)
		return

	with dpg.window(
		tag=tag, label="About AutoVamp",
		no_resize=True, width=360, height=180,
	):
		dpg.add_text(f"AutoVamp v{__version__}")
		dpg.add_spacer(height=4)
		dpg.add_text(
			"Audio player with interactive cues.\n"
			"Useful for practising, rehearsing,\n"
			"or performing with backing tracks.",
		)
		dpg.add_spacer(height=8)
		dpg.add_text("License: GNU GPL v3")
		dpg.add_spacer(height=8)
		dpg.add_button(
			label="Close",
			callback=lambda: dpg.hide_item(tag),
		)


def show_error(message: str) -> None:
	"""Show an error modal with the given message."""
	tag = "error_dialog"
	if dpg.does_item_exist(tag):
		dpg.delete_item(tag)

	with dpg.window(
		tag=tag, label="Error", modal=True,
		no_resize=True, width=400, height=120,
	):
		dpg.add_text(message, wrap=380)
		dpg.add_spacer(height=8)
		dpg.add_button(
			label="OK",
			callback=lambda: dpg.delete_item(tag),
		)


def show_help() -> None:
	"""Show the Help window, loaded from help.txt."""
	tag = "help_dialog"
	if dpg.does_item_exist(tag):
		dpg.show_item(tag)
		return

	text = "Help file not found."
	if os.path.isfile(HELP_PATH):
		with open(HELP_PATH) as f:
			text = f.read()

	with dpg.window(
		tag=tag, label="Help", width=500, height=520,
	):
		dpg.add_text(text)
		dpg.add_spacer(height=8)
		dpg.add_button(
			label="Close",
			callback=lambda: dpg.hide_item(tag),
		)
