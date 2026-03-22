from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from .models import Vamp, PlaybackContext, PlaybackState


class VampEngine:
	"""Audio playback engine with support for vamp loop regions.

	The engine loads an audio file into memory, plays it through
	the default audio output device using sounddevice, and manages
	vamp regions. Vamps are sections of audio that loop repeatedly,
	with configurable behaviours that control how the looping and
	exiting works.

	All mutable state is protected by a lock so that the audio
	callback (which runs on a real-time thread managed by
	PortAudio) and the main thread (which handles user input via
	the CLI) can safely interact.
	"""

	BLOCK_SIZE: int = 512

	def __init__(
			self,
			filepath: str,
			vamps: list[Vamp],
			block_size: int = BLOCK_SIZE,
	) -> None:
		"""Load an audio file and prepare the engine for playback.

		The entire audio file is read into memory as a float32
		numpy array. Vamps are sorted by their start time so they
		can be processed sequentially as the playhead advances.

		Args:
			filepath (str): Path to the audio file to play. Any
				format supported by libsndfile (wav, flac, ogg,
				etc.) is accepted.
			vamps (list[Vamp]): List of Vamp instances defining
				the loop regions.
			block_size (int): Number of audio frames per callback
				invocation. Smaller values reduce latency but
				increase CPU overhead.
		"""
		self._audio_data, self._samplerate_hz = sf.read(
			filepath, dtype="float32", always_2d=True
		)

		self._total_samples: int = self._audio_data.shape[0]
		self._channels: int = self._audio_data.shape[1]
		self._block_size: int = block_size
		# Sort vamps by start time so we can step through them in
		# order as the playhead advances through the file.
		self._vamps: list[Vamp] = sorted(
			vamps, key=lambda v: v.start_time
		)
		# Index of the next vamp to enter. Incremented each time
		# the playhead crosses a vamp's start boundary.
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
		"""The sample rate of the loaded audio file in Hertz."""
		return self._samplerate_hz

	@property
	def duration_seconds(self) -> float:
		"""The total duration of the loaded audio file in seconds."""
		return self._total_samples / self._samplerate_hz

	@property
	def state(self) -> PlaybackState:
		"""Return a thread-safe snapshot of the current playback state.

		This is used by the CLI to read the engine's state without
		holding the lock for an extended period.
		"""
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
		"""An event that is set when playback has finished or been stopped."""
		return self._done

	def play(self) -> None:
		"""Start audio playback.

		Opens a sounddevice output stream and begins sending audio
		data to the default output device. The audio callback runs
		on a separate real-time thread managed by PortAudio.
		"""
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
		"""Stop audio playback and signal that the engine is done.

		Stops and closes the audio stream if one is open, and sets
		the done event so that any threads waiting on it (such as
		the CLI status loop or the key reader) can exit.
		"""
		with self._lock:
			self._is_playing = False

		if self._stream is not None:
			self._stream.stop()
			self._stream.close()
			self._stream = None

		self._done.set()

	def exit_current_vamp(self) -> None:
		"""Request that the active vamp begin its exit process.

		Delegates to the current vamp's behaviour, which decides
		how to handle the exit (for example, jumping immediately
		or finishing the current iteration first).
		"""
		with self._lock:
			if self._is_vamping and self._current_vamp is not None:
				context = self._make_context()

				self._current_vamp.behaviour.on_exit_requested(
					self._current_vamp, context
				)

				self._apply_context(context)

	def _make_context(self) -> PlaybackContext:
		"""Create a PlaybackContext from the engine's current state.

		The returned context is a mutable copy that vamp behaviours
		can modify. Changes are applied back to the engine via
		_apply_context. Must be called while holding the lock.
		"""
		return PlaybackContext(
			position_samples=self._playhead_samples,
			is_vamping=self._is_vamping,
			is_paused=self._is_paused,
			samplerate_hz=self._samplerate_hz,
		)

	def _apply_context(self, context: PlaybackContext) -> None:
		"""Apply changes from a PlaybackContext back to the engine.

		This is called after a vamp behaviour has had the
		opportunity to modify the context. Must be called while
		holding the lock.

		Args:
			context (PlaybackContext): The context whose values
				should be copied into the engine's internal state.
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
		"""Audio callback invoked by PortAudio to fill the buffer.

		This runs on a real-time thread and must not block,
		allocate memory, or perform any operation that could cause
		priority inversion. All state access is protected by the
		engine's lock.

		The callback fills the output buffer by copying samples
		from the loaded audio data. It handles three cases within
		a single buffer:

		1. Normal playback outside of any vamp region.
		2. Playback inside a vamp region, which may trigger vamp
		   entry, exit, or looping callbacks.
		3. End of file, where the remaining buffer is zero-filled.

		Args:
			outdata (np.ndarray): The output buffer to fill with
				audio samples. Its shape is (frames, channels).
			frames (int): The number of frames to write into the
				output buffer.
			time_info (object): Timing information from PortAudio
				(not used).
			status (sd.CallbackFlags): Flags indicating whether an
				underflow or overflow occurred (not used).
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
				# Check if we have reached the end of the audio.
				if self._playhead_samples >= self._total_samples:
					outdata[written_frames:] = 0
					self._is_playing = False
					self._done.set()
					return

				# Check if the playhead has crossed into the next
				# vamp's start boundary. If so, activate that vamp
				# and notify its behaviour.
				if (
						not self._is_vamping
						and self._next_vamp_index < len(self._vamps)
						and self._playhead_samples
						>= self._vamps[
					self._next_vamp_index
				].start_sample(self._samplerate_hz)
				):
					self._current_vamp = self._vamps[
						self._next_vamp_index
					]
					self._is_vamping = True
					self._next_vamp_index += 1

					context = self._make_context()

					self._current_vamp.behaviour.on_vamp_entry(
						self._current_vamp, context
					)

					self._apply_context(context)

					# If the behaviour paused playback on entry
					# (as the CaesuraVamp does), zero-fill the
					# rest of the buffer and return immediately.
					if self._is_paused:
						outdata[written_frames:] = 0
						return

				remaining_frames = frames - written_frames

				if (
						self._is_vamping
						and self._current_vamp is not None
				):
					# We are inside a vamp region. Only copy
					# samples up to the vamp's end boundary.
					end_sample = self._current_vamp.end_sample(
						self._samplerate_hz
					)

					chunk_frames = min(
						remaining_frames,
						end_sample - self._playhead_samples,
					)

					if chunk_frames <= 0:
						# The playhead is at or past the vamp's
						# end boundary. Notify the behaviour,
						# which may loop back to the start or
						# exit the vamp.
						context = self._make_context()
						self._current_vamp.behaviour.on_vamp_exit(
							self._current_vamp, context
						)
						self._apply_context(context)
						continue

					end = written_frames + chunk_frames
					playhead_end = (
							self._playhead_samples + chunk_frames
					)
					outdata[written_frames:end] = (
						self._audio_data[
							self._playhead_samples:playhead_end
						]
					)

					self._playhead_samples += chunk_frames
					written_frames += chunk_frames

					if self._playhead_samples >= end_sample:
						# We have just written up to the vamp's
						# end boundary. Notify the behaviour so
						# it can decide whether to loop or exit.
						context = self._make_context()

						self._current_vamp.behaviour.on_vamp_exit(
							self._current_vamp, context
						)

						self._apply_context(context)

				else:
					# Normal playback outside of any vamp region.
					# Copy samples up to either the start of the
					# next vamp or the end of the file, whichever
					# comes first.
					limit_samples = self._total_samples

					if self._next_vamp_index < len(self._vamps):
						limit_samples = min(
							limit_samples,
							self._vamps[
								self._next_vamp_index
							].start_sample(
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
						self._audio_data[
							self._playhead_samples:playhead_end
						]
					)

					self._playhead_samples += chunk_frames
					written_frames += chunk_frames

			# If the buffer was not completely filled (for example
			# because we ran out of samples between vamp boundaries
			# within a single callback), zero-fill the remainder
			# to avoid outputting garbage.
			if written_frames < frames:
				outdata[written_frames:] = 0
