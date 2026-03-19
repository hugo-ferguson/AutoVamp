from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from .models import Vamp, PlaybackContext, PlaybackState


class VampEngine:
    BLOCK_SIZE: int = 512

    def __init__(self, filepath: str, vamps: list[Vamp], blocksize: int = BLOCK_SIZE) -> None:
        self._data, self._samplerate = sf.read(
            filepath, dtype="float32", always_2d=True
        )

        self._total_samples: int = self._data.shape[0]
        self._channels: int = self._data.shape[1]
        self._blocksize: int = blocksize
        self._vamps: list[Vamp] = sorted(vamps, key=lambda v: v.start)
        self._vamp_index: int = 0
        self._playhead: int = 0
        self._is_vamping: bool = False
        self._is_paused: bool = False
        self._current_vamp: Vamp | None = None
        self._playing: bool = False
        self._lock: threading.Lock = threading.Lock()
        self._done: threading.Event = threading.Event()
        self._stream: sd.OutputStream | None = None

    @property
    def samplerate(self) -> int:
        return self._samplerate

    @property
    def duration_seconds(self) -> float:
        return self._total_samples / self._samplerate

    @property
    def state(self) -> PlaybackState:
        with self._lock:
            return PlaybackState(
                position_samples=self._playhead,
                is_vamping=self._is_vamping,
                is_paused=self._is_paused,
                current_vamp=self._current_vamp,
                playing=self._playing,
            )

    @property
    def done(self) -> threading.Event:
        return self._done

    def play(self) -> None:
        with self._lock:
            self._playing = True

        self._stream = sd.OutputStream(
            samplerate=self._samplerate,
            channels=self._channels,
            blocksize=self._blocksize,
            callback=self._callback,
        )

        self._stream.start()

    def stop(self) -> None:
        with self._lock:
            self._playing = False

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._done.set()

    def exit_current_vamp(self) -> str | None:
        with self._lock:
            if self._is_vamping and self._current_vamp is not None:
                context = self._make_context()

                self._current_vamp.behaviour.on_exit_requested(
                    self._current_vamp, context
                )

                self._apply_context(context)
                return self._current_vamp.behaviour.name
            return None

    def _make_context(self) -> PlaybackContext:
        return PlaybackContext(
            position_samples=self._playhead,
            is_vamping=self._is_vamping,
            is_paused=self._is_paused,
            samplerate_hz=self._samplerate,
        )

    def _apply_context(self, context: PlaybackContext) -> None:
        self._playhead = context.position_samples
        self._is_vamping = context.is_vamping
        self._is_paused = context.is_paused

    def _callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        with self._lock:
            if not self._playing:
                outdata.fill(0)
                return

            if self._is_paused:
                outdata.fill(0)
                return

            written = 0

            while written < frames:
                if self._playhead >= self._total_samples:
                    outdata[written:] = 0
                    self._playing = False
                    self._done.set()
                    return

                if (
                    not self._is_vamping
                    and self._vamp_index < len(self._vamps)
                    and self._playhead
                    >= self._vamps[self._vamp_index].start_sample(self._samplerate)
                ):
                    self._current_vamp = self._vamps[self._vamp_index]
                    self._is_vamping = True
                    self._vamp_index += 1

                    context = self._make_context()

                    self._current_vamp.behaviour.on_vamp_entry(
                        self._current_vamp, context
                    )

                    self._apply_context(context)

                    if self._is_paused:
                        outdata[written:] = 0
                        return

                remaining = frames - written

                if self._is_vamping and self._current_vamp is not None:
                    end_sample = self._current_vamp.end_sample(
                        self._samplerate
                    )

                    chunk = min(remaining, end_sample - self._playhead)

                    if chunk <= 0:
                        context = self._make_context()
                        self._current_vamp.behaviour.on_vamp_exit(
                            self._current_vamp, context
                        )
                        self._apply_context(context)
                        continue

                    outdata[written: written + chunk] = self._data[
                        self._playhead: self._playhead + chunk
                    ]

                    self._playhead += chunk
                    written += chunk

                    if self._playhead >= end_sample:
                        context = self._make_context()

                        self._current_vamp.behaviour.on_vamp_exit(
                            self._current_vamp, context
                        )

                        self._apply_context(context)

                else:
                    limit = self._total_samples

                    if self._vamp_index < len(self._vamps):
                        limit = min(
                            limit,
                            self._vamps[self._vamp_index].start_sample(
                                self._samplerate,
                            ),
                        )

                    chunk = min(remaining, limit - self._playhead)

                    if chunk <= 0:
                        break

                    outdata[written: written + chunk] = self._data[
                        self._playhead: self._playhead + chunk
                    ]

                    self._playhead += chunk
                    written += chunk

            if written < frames:
                outdata[written:] = 0
