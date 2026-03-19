from __future__ import annotations
from datetime import timedelta
import select
import sys
import time
import threading

from ..engine import VampEngine
from ..models import Vamp

_KEY_POLL_TIMEOUT: float = 0.1


class CliApp:
    CLI_REFRESH_INTERVAL_SECONDS: float = 0.05

    EXIT_MESSAGE_DURATION: float = 1.0

    def __init__(self, engine: VampEngine) -> None:
        self._engine: VampEngine = engine
        self._exit_message: str | None = None
        self._exit_message_time: float = 0.0

    def run(self) -> None:
        self._print_header()
        self._start_key_reader()
        self._engine.play()
        self._status_loop()
        self._engine.stop()
        self._key_thread.join(timeout=1.0)
        print("\n  Done.")

    def _print_header(self) -> None:
        print(f"Duration: {self._engine.duration_seconds:.1f}s")
        print(f"Sample rate: {self._engine.samplerate}Hz")
        print(f"─────────────────────────────────────")
        print(f"SPACE = exit current vamp")
        print(f"Q     = quit")
        print(f"─────────────────────────────────────")

    def _start_key_reader(self) -> None:
        def handle_key(char: str) -> bool:
            if char == " ":
                vamp_name = self._engine.exit_current_vamp()
                if vamp_name is not None:
                    self._exit_message = f"EXITING {vamp_name} VAMP"
                    self._exit_message_time = time.monotonic()
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
                        time.sleep(_KEY_POLL_TIMEOUT)
        else:
            def read_keys() -> None:
                import termios
                import tty
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    while not self._engine.done.is_set():
                        ready, _, _ = select.select(
                            [sys.stdin], [], [], _KEY_POLL_TIMEOUT)
                        if ready:
                            if not handle_key(sys.stdin.read(1)):
                                return
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        self._key_thread = threading.Thread(target=read_keys, daemon=True)
        self._key_thread.start()

    def _status_loop(self) -> None:
        while not self._engine.done.is_set():
            state = self._engine.state

            position = timedelta(
                seconds=state.position_samples / self._engine.samplerate
            )

            total = timedelta(seconds=self._engine.duration_seconds)
            position_str = Vamp.format_timestamp(position)
            total_str = Vamp.format_timestamp(total)

            vamp_indicator = ""

            if state.is_vamping and state.current_vamp is not None:
                vamp_start = Vamp.format_timestamp(state.current_vamp.start)
                vamp_end = Vamp.format_timestamp(state.current_vamp.end)
                vamp_indicator = f"  [VAMPING {vamp_start}–{vamp_end}]"

            exit_indicator = ""

            if (
                self._exit_message is not None
                and time.monotonic() - self._exit_message_time < self.EXIT_MESSAGE_DURATION
            ):
                exit_indicator = f"  [{self._exit_message}]"
            else:
                self._exit_message = None

            line = f"\r  {position_str} / {total_str}{vamp_indicator}{exit_indicator}"
            print(f"{line:<80}", end="", flush=True)

            time.sleep(self.CLI_REFRESH_INTERVAL_SECONDS)
