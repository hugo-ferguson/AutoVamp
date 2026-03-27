"""Command-line entry point and argument parsing for AutoVamp.

Handles CLI argument parsing, TOML config loading, cue
construction from user input, and validation before handing
off to the engine and CLI app.
"""

from __future__ import annotations
import argparse
import os
import sys
import tomllib
from . import __version__
from datetime import timedelta
from .models import (
	Cue,
	Jump,
	Continue,
	Safety,
	Caesura,
	CueBehaviour,
	format_timestamp,
	parse_timestamp,
)
from .engine import VampEngine
from .cli.app import CliApp

# Maps behaviour names to their corresponding CueBehaviour
# classes. Used when parsing both CLI arguments and TOML files.
BEHAVIOURS: dict[str, type[CueBehaviour]] = {
	"jump": Jump,
	"continue": Continue,
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
	if behaviour_name == "continue" and "repetitions" in raw:
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


def load_toml(path: str) -> tuple[str, list[Cue]]:
	"""Load an audio file path and cue definitions from a TOML file.

	Args:
		path: Path to the TOML config file.

	Returns:
		A tuple of (audio file path, list of Cues).
	"""
	with open(path, "rb") as f:
		config = tomllib.load(f)

	if "file" not in config:
		_error(f"{path}: missing required 'file' key")

	raw_cues = config.get("cue", [])
	if not raw_cues:
		_error(f"{path}: no [[cue]] entries found")

	cues = [build_cue(c) for c in raw_cues]

	# Resolve the audio file path relative to the TOML file's
	# directory, so that "file = song.wav" works regardless of
	# the user's working directory.
	filepath = config["file"]
	if not os.path.isabs(filepath):
		filepath = os.path.join(os.path.dirname(path), filepath)

	return filepath, cues


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
		# File mode: load everything from the TOML config.
		if args.cues:
			_error(
				"--cue flags cannot be used with a "
				"TOML config file"
			)

		filepath, cues = load_toml(args.file)
	else:
		# Inline mode: audio file with --cue flags.
		filepath = args.file
		if not args.cues:
			_error(
				"at least one --cue is required when "
				"using an audio file directly"
			)

		cues = [
			build_cue(parse_cue_arg(c))
			for c in args.cues
		]

	engine = VampEngine(filepath=filepath, cues=cues)

	# Validate that no cue extends past the end of the file.
	duration_seconds = engine.duration_seconds
	for cue in cues:
		check_time = cue.end_time or cue.start_time
		if check_time.total_seconds() > duration_seconds:
			_error(
				f"cue at {format_timestamp(check_time)} "
				f"exceeds audio duration "
				f"({format_timestamp(
					timedelta(seconds=duration_seconds),
				)})"
			)

	app = CliApp(engine, filename=filepath)
	app.run()


if __name__ == "__main__":
	main()
