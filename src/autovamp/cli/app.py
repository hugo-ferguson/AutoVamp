from __future__ import annotations
from datetime import timedelta
import os
import select
import sys
import time
import threading

from ..engine import VampEngine
from ..models import format_timestamp

# How often (in seconds) the keyboard input thread checks for
# new keypresses. A smaller value makes the interface more
# responsive but uses more CPU.
KEY_POLL_INTERVAL_SECONDS: float = 0.1

# ANSI escape codes used to style terminal output.
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"

# The number of characters wide the progress bar is rendered
# in the terminal.
PROGRESS_BAR_WIDTH_CHARS = 30


class CliApp:
    """Terminal-based interface for controlling audio playback
    with vamp loops.

    This class renders a live-updating status line showing
    playback progress, the current vamp state, and handles
    keyboard input for user interaction. It reads from a
    VampEngine instance which manages the actual audio playback
    and vamp logic.
    """

    # How often (in seconds) the status line redraws. This
    # controls the visual smoothness of the progress bar and
    # timestamp updates.
    CLI_REFRESH_INTERVAL_SECONDS: float = 0.05

    def __init__(
        self, engine: VampEngine, filename: str = "",
    ) -> None:
        """Initialise the CLI application.

        Args:
            engine (VampEngine): The VampEngine instance that
                handles audio playback and vamp behaviour. The
                CLI reads state from this engine and sends user
                commands (exit vamp, quit) back to it.
            filename (str): The path to the audio file being
                played. Used only for display purposes in the
                header. If empty, the file label is omitted from
                the header.
        """
        self._engine: VampEngine = engine
        self._filename: str = filename
        self._key_thread: threading.Thread | None = None
        # Stores the text of the current exit message (for
        # example "EXITING JUMP VAMP") while the user is waiting
        # for a vamp to finish after pressing SPACE. Set to None
        # when no exit is pending.
        self._exit_message: str | None = None

    def run(self) -> None:
        """Run the CLI application from start to finish.

        This is the main entry point. It prints the header,
        starts listening for keyboard input on a background
        thread, begins audio playback, and then blocks on the
        status loop until playback completes or the user quits.
        After the loop exits, it stops the engine and waits for
        the keyboard thread to shut down.
        """
        self._print_header()
        self._start_key_reader()
        self._engine.play()
        self._status_loop()
        self._engine.stop()
        if self._key_thread is not None:
            self._key_thread.join(timeout=1.0)
        print(f"\n\n  {GREEN}{BOLD} Done.{RESET}\n")

    def _print_header(self) -> None:
        """Print the initial header block to the terminal.

        Displays the application title, audio file metadata
        (filename, duration, sample rate), and the available
        keyboard controls.
        """
        title = f"{BOLD}{CYAN} AutoVamp{RESET}"
        file_label = ""
        if self._filename:
            # Only show the base filename, not the full path, to
            # keep the header compact and readable.
            basename = os.path.basename(self._filename)
            file_label = f"  {DIM}File:{RESET} {basename}"

        duration_seconds = self._engine.duration_seconds
        duration_label = (
            f"  {DIM}Duration:{RESET} {duration_seconds:.1f}s"
        )
        samplerate_hz = self._engine.samplerate_hz
        rate_label = (
            f"  {DIM}Sample rate:{RESET} {samplerate_hz}Hz"
        )

        print()
        print(f"  {title}")
        print(f"  {'─' * 38}")
        if file_label:
            print(file_label)
        print(duration_label)
        print(rate_label)
        print()
        print(
            f"  {DIM}SPACE: {RESET} exit vamp\n"
            f"  {DIM}Q: {RESET} quit"
        )
        print(f"  {'─' * 38}")
        print()

    def _start_key_reader(self) -> None:
        """Start a background thread that listens for keyboard input.

        Keyboard reading requires platform-specific code. On
        Windows, we use msvcrt to poll for keypresses. On Unix
        systems, we switch stdin to raw mode using termios so
        that individual characters are delivered immediately
        without waiting for the user to press Enter. The original
        terminal settings are restored when the thread exits.

        The background thread runs as a daemon so it will not
        prevent the process from exiting if something goes wrong.
        """
        def handle_key(char: str) -> bool:
            """Process a single keypress.

            Args:
                char (str): The character that was pressed.

            Returns:
                bool: True if the key reader should continue
                    listening for input, False if the user
                    requested to quit and the thread should stop.
            """
            if char == " ":
                # Ask the engine to begin exiting the current
                # vamp. The engine delegates to the vamp's
                # behaviour, which may exit immediately or allow
                # a set number of remaining iterations first.
                vamp_name = self._engine.exit_current_vamp()
                if vamp_name is not None:
                    self._exit_message = (
                        f"EXITING {vamp_name} VAMP"
                    )
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
                        time.sleep(KEY_POLL_INTERVAL_SECONDS)
        else:
            def read_keys() -> None:
                import termios
                import tty
                fd = sys.stdin.fileno()
                old_settings = termios.tcgetattr(fd)
                try:
                    # Switch to raw mode so we receive each
                    # keypress individually without requiring
                    # the user to press Enter.
                    tty.setraw(fd)
                    while not self._engine.done.is_set():
                        # Use select with a timeout so we can
                        # periodically check whether the engine
                        # has finished, rather than blocking
                        # indefinitely on stdin.
                        ready, _, _ = select.select(
                            [sys.stdin],
                            [],
                            [],
                            KEY_POLL_INTERVAL_SECONDS,
                        )
                        if ready:
                            if not handle_key(
                                sys.stdin.read(1)
                            ):
                                return
                finally:
                    # Always restore the original terminal
                    # settings, even if an exception occurs, so
                    # the user's shell is not left in raw mode.
                    termios.tcsetattr(
                        fd,
                        termios.TCSADRAIN,
                        old_settings,
                    )

        self._key_thread = threading.Thread(
            target=read_keys, daemon=True,
        )
        self._key_thread.start()

    def _build_progress_bar(
        self, fraction: float, is_vamping: bool,
    ) -> str:
        """Build a text-based progress bar with ANSI colour codes.

        The bar uses filled block characters for the completed
        portion and dimmed shade characters for the remaining
        portion. The colour changes to magenta while a vamp is
        active, and is cyan during normal playback.

        Args:
            fraction (float): A value between 0.0 and 1.0
                representing how far through the audio file
                playback has progressed.
            is_vamping (bool): Whether the engine is currently
                inside a vamp region. This changes the colour of
                the progress bar.

        Returns:
            str: A string containing the rendered progress bar
                with ANSI colour codes, ready to be printed to
                the terminal.
        """
        filled_chars = int(fraction * PROGRESS_BAR_WIDTH_CHARS)
        filled_chars = max(
            0, min(filled_chars, PROGRESS_BAR_WIDTH_CHARS)
        )
        empty_chars = PROGRESS_BAR_WIDTH_CHARS - filled_chars

        bar_colour = MAGENTA if is_vamping else CYAN
        return (
            f"{bar_colour}{'█' * filled_chars}"
            f"{DIM}{'░' * empty_chars}{RESET}"
        )

    def _status_loop(self) -> None:
        """Continuously redraw the status line until playback ends.

        This loop runs on the main thread and polls the engine
        for its current state at a regular interval. Each
        iteration overwrites the same terminal line using a
        carriage return, creating a live-updating display that
        shows the progress bar, current timestamp, vamp status,
        and any pending exit message.
        """
        while not self._engine.done.is_set():
            state = self._engine.state

            # Convert the current playhead position from samples
            # to a human-readable timestamp for display.
            position_time = timedelta(
                seconds=(
                    state.position_samples
                    / self._engine.samplerate_hz
                )
            )

            total_duration = timedelta(
                seconds=self._engine.duration_seconds
            )
            position_str = format_timestamp(
                position_time, total_duration,
            )
            total_str = format_timestamp(
                total_duration, total_duration,
            )

            # Calculate how far through the file we are as a
            # fraction, used to determine how much of the
            # progress bar to fill.
            duration_seconds = self._engine.duration_seconds
            if duration_seconds > 0:
                progress_fraction = (
                    state.position_samples
                    / (
                        self._engine.samplerate_hz
                        * duration_seconds
                    )
                )
            else:
                progress_fraction = 0.0
            progress_bar = self._build_progress_bar(
                progress_fraction, state.is_vamping,
            )

            # Show the vamp region timestamps while we are
            # inside a vamp.
            vamp_indicator = ""
            if (
                state.is_vamping
                and state.current_vamp is not None
            ):
                vamp_start_str = format_timestamp(
                    state.current_vamp.start_time,
                    total_duration,
                )
                vamp_end_str = format_timestamp(
                    state.current_vamp.end_time,
                    total_duration,
                )
                vamp_indicator = (
                    f"  {MAGENTA}{BOLD}VAMPING{RESET}"
                    f" {DIM}{vamp_start_str}"
                    f"–{vamp_end_str}{RESET}"
                )

            # The exit message persists for as long as we are
            # still inside the vamp. Once the engine reports that
            # we have left the vamp region, the message is
            # cleared automatically.
            exit_indicator = ""
            if self._exit_message is not None:
                if state.is_vamping:
                    exit_indicator = (
                        f"  {YELLOW}"
                        f"{self._exit_message}"
                        f"{RESET}"
                    )
                else:
                    self._exit_message = None

            # Compose and print the status line. The \r moves
            # the cursor back to the start of the line, and
            # \033[K erases any leftover characters from the
            # previous render (for example when the vamp
            # indicator disappears and the line becomes shorter).
            time_display = (
                f"{BOLD}{position_str}{RESET}"
                f" {DIM}/{RESET} {total_str}"
            )
            line = (
                f"\r  {progress_bar}  {time_display}"
                f"{vamp_indicator}{exit_indicator}"
            )
            print(
                f"{line}\033[K", end="", flush=True,
            )

            time.sleep(self.CLI_REFRESH_INTERVAL_SECONDS)
