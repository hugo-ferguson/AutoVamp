"""Audio playback engine with vamp loop support.

Loads an audio file into memory, plays it through the default
output device, and manages vamp regions that loop sections of
audio based on configurable behaviours.
"""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from .models import Vamp, PlaybackContext, PlaybackState


class VampEngine:
	"""Audio playback engine with vamp loop regions.

	Loads an audio file, plays it via sounddevice, and manages
	vamp regions with configurable looping behaviours. All
	mutable state is lock-protected so the real-time PortAudio
	callback and the main thread can safely interact.
	"""

	BLOCK_SIZE: int = 512

	def __init__(
			self,
			filepath: str,
			vamps: list[Vamp],
			block_size: int = BLOCK_SIZE,
	) -> None:
		"""Load an audio file and prepare for playback.

		Args:
			filepath: Path to the audio file (any format
				supported by libsndfile).
			vamps: List of Vamp instances defining loop regions.
			block_size: Audio frames per callback. Smaller values
				reduce latency but increase CPU overhead.
		"""
		self._audio_data, self._samplerate_hz = sf.read(
			filepath, dtype="float32", always_2d=True
		)

		self._total_samples: int = self._audio_data.shape[0]
		self._channels: int = self._audio_data.shape[1]
		self._block_size: int = block_size
		# Sorted by start time for sequential processing.
		self._vamps: list[Vamp] = sorted(
			vamps, key=lambda v: v.start_time
		)
		# Index of the next vamp to enter.
		self._next_vamp_index: int = 0
		self._playhead_samples: int = 0
		self._is_vamping: bool = False
		self._is_paused: bool = False
		self._current_vamp: Vamp | None = None
		self._is_playing: bool = False
		self._lock: threading.Lock = threading.Lock()
		self._done: threading.Event = threading.Event()
		self._stream: sd.OutputStream | None = None

	@property
	def samplerate_hz(self) -> int:
		"""The sample rate of the loaded audio in Hertz."""
		return self._samplerate_hz

	@property
	def duration_seconds(self) -> float:
		"""Total duration of the loaded audio in seconds."""
		return self._total_samples / self._samplerate_hz

	@property
	def vamps(self) -> list[Vamp]:
		"""The list of vamp regions, sorted by start time."""
		return self._vamps

	@property
	def state(self) -> PlaybackState:
		"""Thread-safe snapshot of the current playback state."""
		with self._lock:
			return PlaybackState(
				position_samples=self._playhead_samples,
				is_vamping=self._is_vamping,
				is_paused=self._is_paused,
				is_playing=self._is_playing,
				current_vamp=self._current_vamp,
			)

	@property
	def done(self) -> threading.Event:
		"""Set when playback has finished or been stopped."""
		return self._done

	def play(self) -> None:
		"""Start audio playback via the default output device."""
		with self._lock:
			self._is_playing = True

		self._stream = sd.OutputStream(
			samplerate=self._samplerate_hz,
			channels=self._channels,
			blocksize=self._block_size,
			callback=self._audio_callback,
		)

		self._stream.start()

	def stop(self) -> None:
		"""Stop playback and signal that the engine is done."""
		with self._lock:
			self._is_playing = False

		if self._stream is not None:
			self._stream.stop()
			self._stream.close()
			self._stream = None

		self._done.set()

	def toggle_pause(self) -> None:
		"""Toggle between paused and playing states."""
		with self._lock:
			self._is_paused = not self._is_paused

	def seek(self, offset_seconds: float) -> None:
		"""Seek the playhead by a relative offset in seconds.

		Clamps to valid bounds and recalculates vamp state.

		Args:
			offset_seconds: Seconds to seek (negative for
				backwards, positive for forwards).
		"""
		with self._lock:
			prev_vamp = self._current_vamp

			offset_samples = int(offset_seconds * self._samplerate_hz)
			new_pos = self._playhead_samples + offset_samples
			new_pos = max(0, min(new_pos, self._total_samples))
			self._playhead_samples = new_pos

			# Recalculate which vamp we are in or approaching.
			self._is_vamping = False
			self._current_vamp = None
			self._next_vamp_index = 0

			for i, vamp in enumerate(self._vamps):
				start = vamp.start_sample(self._samplerate_hz)
				end = vamp.end_sample(self._samplerate_hz)

				if start <= new_pos < end:
					self._current_vamp = vamp
					self._is_vamping = True
					self._next_vamp_index = i + 1

					# Only trigger entry if we changed vamps.
					if vamp is not prev_vamp:
						context = self._make_context()
						vamp.behaviour.on_vamp_entry(
							vamp, context,
						)
						self._apply_context(context)
					break
				elif new_pos < start:
					self._next_vamp_index = i
					break
			else:
				self._next_vamp_index = len(self._vamps)

	def exit_current_vamp(self) -> None:
		"""Ask the active vamp's behaviour to begin exiting."""
		with self._lock:
			if self._is_vamping and self._current_vamp is not None:
				context = self._make_context()

				self._current_vamp.behaviour.on_exit_requested(
					self._current_vamp, context
				)

				self._apply_context(context)

	def _make_context(self) -> PlaybackContext:
		"""Create a mutable PlaybackContext from the current state.

		Behaviours modify this context, then changes are applied
		back via _apply_context. Must hold the lock.

		Returns:
			A PlaybackContext reflecting the engine's state.
		"""
		return PlaybackContext(
			position_samples=self._playhead_samples,
			is_vamping=self._is_vamping,
			is_paused=self._is_paused,
			samplerate_hz=self._samplerate_hz,
		)

	def _apply_context(self, context: PlaybackContext) -> None:
		"""Apply a behaviour-modified context back to the engine.

		Must hold the lock.

		Args:
			context: The context whose values should be copied
				into the engine's internal state.
		"""
		self._playhead_samples = context.position_samples
		self._is_vamping = context.is_vamping
		self._is_paused = context.is_paused

	def _audio_callback(
			self,
			outdata: np.ndarray,
			frames: int,
			time_info: object,
			status: sd.CallbackFlags,
	) -> None:
		"""PortAudio callback that fills the output buffer.

		Runs on a real-time thread. Handles normal playback,
		vamp region looping, and end-of-file zero-filling.

		Args:
			outdata: The output buffer to fill. Shape is
				(frames, channels).
			frames: Number of frames to write into the buffer.
			time_info: Timing information from PortAudio
				(not used).
			status: Flags indicating underflow or overflow
				(not used).
		"""
		with self._lock:
			if not self._is_playing:
				outdata.fill(0)
				return

			if self._is_paused:
				outdata.fill(0)
				return

			written_frames = 0

			while written_frames < frames:
				if self._playhead_samples >= self._total_samples:
					outdata[written_frames:] = 0
					self._is_playing = False
					self._done.set()
					return

				# If playback has moved into the next vamp,
				# activate it.
				if (
						not self._is_vamping
						and self._next_vamp_index < len(self._vamps)
						and self._playhead_samples
						>= self._vamps[
					self._next_vamp_index
				].start_sample(self._samplerate_hz)
				):
					self._current_vamp = self._vamps[self._next_vamp_index]
					self._is_vamping = True
					self._next_vamp_index += 1

					context = self._make_context()

					self._current_vamp.behaviour.on_vamp_entry(
						self._current_vamp, context
					)

					self._apply_context(context)

					# If the behaviour paused on entry (as
					# Caesura does), zero-fill and return.
					if self._is_paused:
						outdata[written_frames:] = 0
						return

				remaining_frames = frames - written_frames

				if self._is_vamping and self._current_vamp is not None:
					# Inside a vamp: copy up to the end boundary.
					end_sample = self._current_vamp.end_sample(
						self._samplerate_hz
					)

					chunk_frames = min(
						remaining_frames,
						end_sample - self._playhead_samples,
					)

					if chunk_frames <= 0:
						# At or past the vamp end boundary.
						# Let the behaviour decide: loop or exit.
						context = self._make_context()
						self._current_vamp.behaviour.on_vamp_exit(
							self._current_vamp, context
						)
						self._apply_context(context)
						continue

					end = written_frames + chunk_frames
					playhead_end = (self._playhead_samples + chunk_frames)
					outdata[written_frames:end] = (
						self._audio_data[
							self._playhead_samples:playhead_end
						]
					)

					self._playhead_samples += chunk_frames
					written_frames += chunk_frames

					if self._playhead_samples >= end_sample:
						# Reached the vamp end boundary. Let the
						# behaviour decide: loop or exit.
						context = self._make_context()

						self._current_vamp.behaviour.on_vamp_exit(
							self._current_vamp, context
						)

						self._apply_context(context)

				else:
					# Normal playback: copy up to the next vamp
					# start or end of file, whichever is first.
					limit_samples = self._total_samples

					if self._next_vamp_index < len(self._vamps):
						limit_samples = min(
							limit_samples,
							self._vamps[self._next_vamp_index].start_sample(
								self._samplerate_hz,
							),
						)

					chunk_frames = min(
						remaining_frames,
						limit_samples - self._playhead_samples,
					)

					if chunk_frames <= 0:
						break

					end = written_frames + chunk_frames
					playhead_end = (
							self._playhead_samples + chunk_frames
					)
					outdata[written_frames:end] = (
						self._audio_data[self._playhead_samples:playhead_end]
					)

					self._playhead_samples += chunk_frames
					written_frames += chunk_frames

			# Zero-fill any remaining buffer space.
			if written_frames < frames:
				outdata[written_frames:] = 0
