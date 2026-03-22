"""Command-line entry point and argument parsing for AutoVamp.

This module handles CLI argument parsing, TOML config loading,
vamp construction from user input, and validation before handing
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
	Vamp,
	JumpVamp,
	ContinueVamp,
	Safety,
	Caesura,
	VampBehaviour,
	format_timestamp,
	parse_timestamp,
)
from .engine import VampEngine
from .cli.app import CliApp

# Maps behaviour names to their corresponding VampBehaviour
# classes. Used when parsing both CLI arguments and TOML files.
BEHAVIOURS: dict[str, type[VampBehaviour]] = {
	"jump": JumpVamp,
	"continue": ContinueVamp,
	"safety": Safety,
	"caesura": Caesura,
}


def _error(message: str) -> None:
	"""Print an error message to stdout and exit with code 1."""
	print(f"Error: {message}")
	raise SystemExit(1)


def build_vamp(raw: dict[str, str]) -> Vamp:
	"""Build a Vamp from a dict with start, end, and behaviour keys.

	This is the shared parsing path used by both TOML config
	files and inline --vamp CLI arguments.

	Args:
		raw: A dict with 'start', 'end', and 'behaviour' keys.

	Returns:
		A validated Vamp instance.
	"""
	# Set subtraction gives us any required keys not present in raw.
	missing = {"start", "end", "behaviour"} - raw.keys()
	if missing:
		_error(
			f"vamp definition missing required keys: "
			f"{', '.join(sorted(missing))}"
		)

	start_time = parse_timestamp(raw["start"])
	end_time = parse_timestamp(raw["end"])

	if start_time >= end_time:
		_error(
			f"vamp start ({raw['start']}) must be "
			f"before end ({raw['end']})"
		)

	behaviour_name = raw["behaviour"]
	if behaviour_name not in BEHAVIOURS:
		_error(
			f"unknown vamp behaviour '{behaviour_name}', "
			f"expected one of: {', '.join(BEHAVIOURS)}"
		)

	return Vamp(
		start_time=start_time,
		end_time=end_time,
		behaviour=BEHAVIOURS[behaviour_name](),
	)


def load_toml(path: str) -> tuple[str, list[Vamp]]:
	"""Load an audio file path and vamp definitions from a TOML file.

	Args:
		path: Path to the TOML config file.

	Returns:
		A tuple of (audio file path, list of Vamps).
	"""
	with open(path, "rb") as f:
		config = tomllib.load(f)

	if "file" not in config:
		_error(f"{path}: missing required 'file' key")

	raw_vamps = config.get("vamp", [])
	if not raw_vamps:
		_error(f"{path}: no [[vamp]] entries found")

	vamps = [build_vamp(v) for v in raw_vamps]

	# Resolve the audio file path relative to the TOML file's
	# directory, so that "file = song.wav" works regardless of
	# the user's working directory.
	filepath = config["file"]
	if not os.path.isabs(filepath):
		filepath = os.path.join(os.path.dirname(path), filepath)

	return filepath, vamps


def parse_vamp_arg(arg: str) -> dict[str, str]:
	"""Parse a --vamp argument in key=value,key=value format.

	Args:
		arg: A string like 'start=0:01:30,end=0:02:00,behaviour=jump'.

	Returns:
		A dict with the parsed key-value pairs.
	"""
	result: dict[str, str] = {}

	for pair in arg.split(","):
		if "=" not in pair:
			_error(
				f"invalid --vamp format: '{pair}', "
				f"expected key=value"
			)
		key, _, value = pair.partition("=")
		result[key.strip()] = value.strip()

	return result


def parse_args() -> argparse.Namespace:
	"""Parse and return command-line arguments."""
	parser = argparse.ArgumentParser(
		description="Audio player with vamp loops",
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
		"--vamp",
		action="append",
		dest="vamps",
		metavar="start=T,end=T,behaviour=TYPE",
		help=(
			"Define a vamp inline. Can be repeated. "
			"Example: --vamp start=0:01:30,end=0:02:00,behaviour=jump"
		),
	)

	return parser.parse_args()


def main() -> None:
	"""Entry point for the AutoVamp command-line application."""
	args = parse_args()

	if args.file.endswith(".toml"):
		# File mode: load everything from the TOML config.
		if args.vamps:
			_error(
				"--vamp flags cannot be used with a "
				"TOML config file"
			)

		filepath, vamps = load_toml(args.file)
	else:
		# Inline mode: audio file with --vamp flags.
		filepath = args.file
		if not args.vamps:
			_error(
				"at least one --vamp is required when "
				"using an audio file directly"
			)

		vamps = [
			build_vamp(parse_vamp_arg(v))
			for v in args.vamps
		]

	engine = VampEngine(filepath=filepath, vamps=vamps)

	# Validate that no vamp extends past the end of the file.
	duration_seconds = engine.duration_seconds
	for vamp in vamps:
		if vamp.end_time.total_seconds() > duration_seconds:
			_error(
				f"vamp end ({format_timestamp(vamp.end_time)}) "
				f"exceeds audio duration "
				f"({format_timestamp(
					timedelta(seconds=duration_seconds),
				)})"
			)

	app = CliApp(engine, filename=filepath)
	app.run()


if __name__ == "__main__":
	main()
