from __future__ import annotations
import argparse
from datetime import timedelta
from .models import Vamp, JumpVamp, ContinueVamp, SafetyVamp, CaesuraVamp, VampBehaviour
from .engine import VampEngine
from .cli.app import CliApp


BEHAVIOURS: dict[str, type[VampBehaviour]] = {
    "jump": JumpVamp,
    "continue": ContinueVamp,
    "safety": SafetyVamp,
    "caesura": CaesuraVamp,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audio player with vamp loops")

    parser.add_argument(
        "file", help="Audio file to play (wav, flac, ogg, mp3)")

    parser.add_argument(
        "--start",
        required=True,
        help="Vamp start timestamp (HH:MM:SS.mmm)",
    )

    parser.add_argument(
        "--end",
        required=True,
        help="Vamp end timestamp (HH:MM:SS.mmm)",
    )

    parser.add_argument(
        "--behaviour",
        required=True,
        choices=list(BEHAVIOURS.keys()),
        help="jump, continue, safety, or caesura",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = Vamp.parse_timestamp(args.start)
    end = Vamp.parse_timestamp(args.end)

    if start >= end:
        print(
            f"Error: --start ({args.start}) must be before --end ({args.end})")
        raise SystemExit(1)

    behaviour = BEHAVIOURS[args.behaviour]()
    vamp = Vamp(start=start, end=end, behaviour=behaviour)
    engine = VampEngine(filepath=args.file, vamps=[vamp])

    duration = engine.duration_seconds
    if end.total_seconds() > duration:
        print(
            f"Error: --end ({args.end}) exceeds audio duration "
            f"({Vamp.format_timestamp(timedelta(seconds=duration))})"
        )
        raise SystemExit(1)

    app = CliApp(engine)
    app.run()


if __name__ == "__main__":
    main()
