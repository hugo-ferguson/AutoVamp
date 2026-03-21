from __future__ import annotations
import argparse
from . import __version__
from datetime import timedelta
from .models import (
    Vamp,
    JumpVamp,
    ContinueVamp,
    SafetyVamp,
    CaesuraVamp,
    VampBehaviour,
    format_timestamp,
    parse_timestamp,
)
from .engine import VampEngine
from .cli.app import CliApp


# Maps command-line behaviour names to their corresponding
# VampBehaviour classes. Users pass one of these names via the
# --behaviour flag.
BEHAVIOURS: dict[str, type[VampBehaviour]] = {
    "jump": JumpVamp,
    "continue": ContinueVamp,
    "safety": SafetyVamp,
    "caesura": CaesuraVamp,
}


def parse_args() -> argparse.Namespace:
    """Parse and return command-line arguments.

    Returns:
        argparse.Namespace: A namespace containing the audio file
            path, vamp start and end timestamps, and the selected
            behaviour name.
    """
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
        help="Audio file to play (wav, flac, ogg, mp3)",
    )

    parser.add_argument(
        "--start",
        required=True,
        help="Vamp start timestamp (S, MM:SS, or HH:MM:SS, with optional .mmm)",
    )

    parser.add_argument(
        "--end",
        required=True,
        help="Vamp end timestamp (S, MM:SS, or HH:MM:SS, with optional .mmm)",
    )

    parser.add_argument(
        "--behaviour",
        required=True,
        choices=list(BEHAVIOURS.keys()),
        help="jump, continue, safety, or caesura",
    )

    return parser.parse_args()


def main() -> None:
    """Entry point for the AutoVamp command-line application.

    Parses command-line arguments, validates the vamp timestamps
    against each other and the audio file's duration, then
    launches the CLI playback interface.
    """
    args = parse_args()
    start_time = parse_timestamp(args.start)
    end_time = parse_timestamp(args.end)

    # Validate that the start timestamp comes before the end.
    if start_time >= end_time:
        print(
            f"Error: --start ({args.start}) must be "
            f"before --end ({args.end})"
        )
        raise SystemExit(1)

    behaviour = BEHAVIOURS[args.behaviour]()
    vamp = Vamp(
        start_time=start_time,
        end_time=end_time,
        behaviour=behaviour,
    )
    engine = VampEngine(filepath=args.file, vamps=[vamp])

    # Validate that the vamp's end timestamp does not exceed the
    # length of the audio file. This check happens after loading
    # the file because we need the engine to know the actual
    # duration.
    duration_seconds = engine.duration_seconds
    if end_time.total_seconds() > duration_seconds:
        print(
            f"Error: --end ({args.end}) exceeds audio "
            f"duration ({format_timestamp(
                timedelta(seconds=duration_seconds)
            )})"
        )
        raise SystemExit(1)

    app = CliApp(engine, filename=args.file)
    app.run()


if __name__ == "__main__":
    main()
