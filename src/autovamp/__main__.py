"""Command-line entry point and argument parsing for AutoVamp.

Handles CLI argument parsing, TOML config loading, track and
cue construction from user input, and validation before handing
off to the CLI app.
"""

from __future__ import annotations
import argparse
import os
import tomllib
from . import __version__
from .models import (
	Cue,
	Track,
	Jump,
	Continue,
	Repeat,
	Safety,
	Caesura,
	CueBehaviour,
	parse_timestamp,
)
from .cli.app import CliApp

# Maps behaviour names to their corresponding CueBehaviour
# classes. Used when parsing both CLI arguments and TOML files.
BEHAVIOURS: dict[str, type[CueBehaviour]] = {
	"jump": Jump,
	"continue": Continue,
	"repeat": Repeat,
	"safety": Safety,
	"caesura": Caesura,
}


def _error(message: str) -> None:
	"""Print an error message to stdout and exit with code 1."""
	print(f"Error: {message}")
	raise SystemExit(1)


def build_cue(raw: dict[str, str]) -> Cue:
	"""Build a Cue from a dict with start, end, and behaviour keys.

	Shared parsing path used by both TOML config files and
	inline --cue CLI arguments. The 'end' key is optional for
	point-in-time behaviours like caesura.

	Args:
		raw: A dict with 'start', 'behaviour', and optionally
			'end' keys.

	Returns:
		A validated Cue instance.
	"""
	missing = {"start", "behaviour"} - raw.keys()
	if missing:
		_error(
			f"cue definition missing required keys: "
			f"{', '.join(sorted(missing))}"
		)

	behaviour_name = raw["behaviour"]
	if behaviour_name not in BEHAVIOURS:
		_error(
			f"unknown cue behaviour '{behaviour_name}', "
			f"expected one of: {', '.join(BEHAVIOURS)}"
		)

	start_time = parse_timestamp(raw["start"])

	end_time = None
	if "end" in raw:
		end_time = parse_timestamp(raw["end"])
		if start_time >= end_time:
			_error(
				f"cue start ({raw['start']}) must be "
				f"before end ({raw['end']})"
			)
	elif behaviour_name != "caesura":
		_error(
			f"'end' is required for behaviour "
			f"'{behaviour_name}'"
		)

	# Build the behaviour, passing any extra options it supports.
	kwargs: dict = {}
	if behaviour_name == "repeat" and "repetitions" in raw:
		try:
			kwargs["repetitions"] = int(raw["repetitions"])
		except ValueError:
			_error(
				f"'repetitions' must be an integer, "
				f"got '{raw['repetitions']}'"
			)

	return Cue(
		start_time=start_time,
		behaviour=BEHAVIOURS[behaviour_name](**kwargs),
		end_time=end_time,
	)


def _resolve_path(filepath: str, toml_dir: str) -> str:
	"""Resolve a file path relative to the TOML file's directory.

	Args:
		filepath: The path from the config file.
		toml_dir: The directory containing the TOML file.

	Returns:
		An absolute or working-directory-relative path.
	"""
	if os.path.isabs(filepath):
		return filepath
	return os.path.join(toml_dir, filepath)


def load_toml(path: str) -> list[Track]:
	"""Load tracks from a TOML config file.

	Supports two formats: the multi-track format with [[track]]
	entries, and the legacy single-file format with a top-level
	'file' key and [[cue]] entries.

	Args:
		path: Path to the TOML config file.

	Returns:
		A list of Track instances.
	"""
	with open(path, "rb") as f:
		config = tomllib.load(f)

	toml_dir = os.path.dirname(path)

	if "track" in config:
		raw_tracks = config["track"]
		if not raw_tracks:
			_error(f"{path}: no [[track]] entries found")

		tracks: list[Track] = []
		for raw_track in raw_tracks:
			if "file" not in raw_track:
				_error(
					f"{path}: [[track]] entry missing "
					f"required 'file' key"
				)

			filepath = _resolve_path(raw_track["file"], toml_dir)
			cues = [build_cue(c) for c in raw_track.get("cue", [])]
			autostart = raw_track.get("autostart", False)
			tracks.append(Track(filepath, cues, autostart))

		return tracks

	# Legacy single-file format.
	if "file" not in config:
		_error(f"{path}: missing 'file' or [[track]] entries")

	raw_cues = config.get("cue", [])
	if not raw_cues:
		_error(f"{path}: no [[cue]] entries found")

	filepath = _resolve_path(config["file"], toml_dir)
	cues = [build_cue(c) for c in raw_cues]
	return [Track(filepath, cues)]


def parse_cue_arg(arg: str) -> dict[str, str]:
	"""Parse a --cue argument in key=value,key=value format.

	Args:
		arg: A string like 'start=0:01:30,end=0:02:00,behaviour=jump'.

	Returns:
		A dict with the parsed key-value pairs.
	"""
	result: dict[str, str] = {}

	for pair in arg.split(","):
		if "=" not in pair:
			_error(
				f"invalid --cue format: '{pair}', "
				f"expected key=value"
			)
		key, _, value = pair.partition("=")
		result[key.strip()] = value.strip()

	return result


def parse_args() -> argparse.Namespace:
	"""Parse and return command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Audio player with cue regions",
	)

	parser.add_argument(
		"--version",
		action="version",
		version=f"autovamp {__version__}",
	)

	parser.add_argument(
		"file",
		help=(
			"Audio file (wav, flac, ogg, mp3) or "
			"TOML config file"
		),
	)

	parser.add_argument(
		"--cue",
		action="append",
		dest="cues",
		metavar="start=T,end=T,behaviour=TYPE",
		help=(
			"Define a cue inline. Can be repeated. "
			"'end' is optional for caesura. "
			"Example: --cue start=0:01:30,end=0:02:00,"
			"behaviour=jump"
		),
	)

	return parser.parse_args()


def main() -> None:
	"""Entry point for the AutoVamp command-line application."""
	args = parse_args()

	if args.file.endswith(".toml"):
		if args.cues:
			_error(
				"--cue flags cannot be used with a "
				"TOML config file"
			)

		tracks = load_toml(args.file)
	else:
		# Inline mode: single audio file with --cue flags.
		if not args.cues:
			_error(
				"at least one --cue is required when "
				"using an audio file directly"
			)

		cues = [
			build_cue(parse_cue_arg(c))
			for c in args.cues
		]
		tracks = [Track(filepath=args.file, cues=cues)]

	app = CliApp(tracks)
	app.run()


if __name__ == "__main__":
	main()
