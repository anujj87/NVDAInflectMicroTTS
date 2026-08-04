# addon/synthDrivers/inflectMicroTTS.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
# See the file COPYING.txt for more details.
"""NVDA speech synthesizer driver for the Inflect Micro v2 neural TTS model.

The engine (addon/synthDrivers/_inflectMicro) bundles the ONNX model and all
Python dependencies, so this driver works completely offline.

Voices: the bundled Inflect Micro v2 (24 kHz) plus any additional voices
of the same Inflect TTS family downloaded from the add-on settings panel.
Settings: voice, rate, rate boost, volume, variation and seed.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from collections.abc import Iterator
from itertools import chain

import config
import nvwave
from autoSettingsUtils.driverSetting import NumericDriverSetting
from logHandler import log
from speech.commands import IndexCommand
from speech.types import SpeechSequence
from synthDriverHandler import (
	SynthDriver,
	VoiceInfo,
	synthDoneSpeaking,
	synthIndexReached,
)

from ._inflectMicro.provision import BUNDLED_VOICE_ID, installedVoiceIds


#: (cancelEvent, firstPiece, restStream) of the next queued utterance,
#: pre-synthesized while the current one was still playing so consecutive
#: voice outputs join without a dead-air gap.
Prefetched = tuple[
	threading.Event,
	tuple[bytes, list[int]],
	Iterator[tuple[bytes, list[int]]],
]


class SynthDriver(SynthDriver):
	name = "inflectMicroTTS"
	# Translators: Name of the speech synthesizer shown in NVDA's settings.
	description = _("Inflect Micro v2")

	supportedSettings = (
		SynthDriver.VoiceSetting(),
		SynthDriver.RateSetting(),
		SynthDriver.RateBoostSetting(),
		NumericDriverSetting(
			"volume",
			# Translators: Label for the volume setting of this synthesizer.
			_("V&olume"),
			availableInSettingsRing=True,
			defaultVal=100,
			minVal=0,
			maxVal=100,
			minStep=1,
			normalStep=5,
		),
		NumericDriverSetting(
			"variation",
			# Translators: Label for the acoustic variation setting.
			_("&Variation"),
			availableInSettingsRing=True,
			defaultVal=67,
			minVal=0,
			maxVal=100,
			minStep=1,
			normalStep=5,
		),
		NumericDriverSetting(
			"seed",
			# Translators: Label for the random seed setting.
			_("&Seed"),
			availableInSettingsRing=False,
			defaultVal=7,
			minVal=0,
			maxVal=100,
			minStep=1,
		),
	)
	#: Upper speed multiplier (rate 100) without rate boost.
	NORMAL_MAX_SPEED = 2.0
	#: Upper speed multiplier (rate 100) with rate boost enabled. The
	#: duration predictor scales durations by ``1/speed``, so 4.0 keeps the
	#: model inside a sane range while still roughly doubling the ceiling.
	BOOSTED_MAX_SPEED = 4.0
	_rateBoost = False
	# NVDA's speech manager inserts IndexCommands into every utterance (an
	# end-of-utterance marker, plus callbacks such as say-all's lineReached)
	# and requires the synthesizer to report both notifications; without
	# synthIndexReached the manager thinks the synth is always busy and
	# falls back to interrupting, which chops the tail of the current
	# sentence.
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}

	@classmethod
	def check(cls) -> bool:
		# The synthesizer and its model ship inside the add-on, so the
		# driver is always available.
		return True

	def __init__(self) -> None:
		super().__init__()
		self._wavePlayer: nvwave.WavePlayer | None = None
		self._wavePlayerLock = threading.Lock()
		# All utterances are spoken one at a time by a single worker
		# thread. NVDA's speech manager pushes several speak() calls back
		# to back while reading long text (say-all), relying on the synth
		# to serialize them; spawning one thread per call fed the same
		# WavePlayer concurrently and made sentences overlap. Each queued
		# item carries its own cancel event so cancel() can drop it.
		self._speechQueue: deque[tuple[str, list[tuple[int, int]], threading.Event]] = deque()
		self._speechQueueLock = threading.Lock()
		self._queueNotEmpty = threading.Event()
		self._stopRequested = threading.Event()
		# The cancel event of the utterance the worker is currently
		# processing (or of the last utterance it finished).
		self._generationCanceled = threading.Event()
		self._rate = 50
		self._rateBoost = False
		self._volume = 100
		self._variation = 67
		self._seed = 7
		self._voice = BUNDLED_VOICE_ID
		# Load the model and warm the eSpeak/ONNX pipelines in the
		# background so the first utterance starts speaking immediately
		# instead of paying the ~0.5 s cold start.
		threading.Thread(
			target=self._preloadEngine,
			daemon=True,
			name="inflectMicroTTS-preload",
		).start()
		threading.Thread(
			target=self._speechLoop,
			daemon=True,
			name="inflectMicroTTS-speech",
		).start()

	def _preloadEngine(self) -> None:
		try:
			from ._inflectMicro.engine import getEngine

			getEngine(self.voice).warmUp()
		except Exception:
			log.debugWarning(
				"Inflect Micro TTS: engine preload failed; the first utterance will pay the cold start",
				exc_info=True,
			)

	# ---------------- voice ----------------

	def _get_voice(self) -> str:
		return self._voice

	def _set_voice(self, value: str) -> None:
		self._voice = value if value in self._getAvailableVoices() else BUNDLED_VOICE_ID

	def _getAvailableVoices(self) -> OrderedDict[str, VoiceInfo]:
		"""The voices whose model files are present (bundled voice first)."""
		from ._inflectMicro.voices import voiceDisplayName

		voices: OrderedDict[str, VoiceInfo] = OrderedDict()
		for voiceId in installedVoiceIds():
			voices[voiceId] = VoiceInfo(voiceId, voiceDisplayName(voiceId), language="en")
		return voices

	def _get_availableVoices(self) -> OrderedDict[str, VoiceInfo]:
		# Rebuilt on every access (instead of the base class's caching)
		# so that a voice downloaded from the add-on settings panel shows
		# up in NVDA's voice list without restarting NVDA.
		return self._getAvailableVoices()

	# ---------------- settings ----------------

	def _get_rate(self) -> int:
		return self._rate

	def _set_rate(self, value: int) -> None:
		self._rate = max(0, min(100, int(value)))

	def _get_rateBoost(self) -> bool:
		return self._rateBoost

	def _set_rateBoost(self, enable: bool) -> None:
		if enable == self._rateBoost:
			return
		# The mapping is only consulted at synthesis time in
		# _speedForRate, so toggling the flag is enough to move speech
		# onto the boosted scale. Re-assigning the same rate value mirrors
		# NVDA's own drivers (eSpeak, Windows OneCore), which push the
		# rate to the underlying synthesizer immediately on set.
		rate = self._rate
		self._rateBoost = enable
		self.rate = rate

	def _get_volume(self) -> int:
		return self._volume

	def _set_volume(self, value: int) -> None:
		self._volume = max(0, min(100, int(value)))

	def _get_variation(self) -> int:
		return self._variation

	def _set_variation(self, value: int) -> None:
		self._variation = max(0, min(100, int(value)))

	def _get_seed(self) -> int:
		return self._seed

	def _set_seed(self, value: int) -> None:
		self._seed = max(0, min(100, int(value)))

	# ---------------- audio ----------------

	def _getWavePlayer(self, sampleRate: int) -> nvwave.WavePlayer | None:
		if self._stopRequested.is_set():
			# terminate() may have torn the audio down; never create a
			# fresh player behind its back or speech would continue after
			# the synthesizer was unloaded.
			return None
		with self._wavePlayerLock:
			if self._stopRequested.is_set():
				return None
			if self._wavePlayer is None:
				self._wavePlayer = nvwave.WavePlayer(
					channels=1,
					samplesPerSec=sampleRate,
					bitsPerSample=16,
					outputDevice=config.conf["audio"]["outputDevice"],
				)
			return self._wavePlayer

	def _speedForRate(self) -> float:
		# Map NVDA's rate (0..100) onto Inflect's speed range (0.5..2.0),
		# so that NVDA's default rate of 30 corresponds to 1.0x. With rate
		# boost enabled the top end of the range is extended (0.5..4.0), so
		# the same rate position speaks faster, exactly like NVDA's own
		# drivers (e.g. Windows OneCore) when their boost is on.
		if self._rateBoost:
			return 0.5 + (self.rate / 100.0) * (self.BOOSTED_MAX_SPEED - 0.5)
		return min(self.NORMAL_MAX_SPEED, 0.5 + (self.rate / 100.0) * (5.0 / 3.0))

	# ---------------- SynthDriver API ----------------

	def speak(self, speechSequence: SpeechSequence) -> None:
		# NVDA marks points of interest (end of utterance, say-all line
		# callbacks, ...) with IndexCommands. Collect them with the
		# character offset at which they occur so the worker can report
		# synthIndexReached as the audio passes each one.
		textParts: list[str] = []
		indexes: list[tuple[int, int]] = []
		charCount = 0
		for item in speechSequence:
			if isinstance(item, str):
				# Remove NVDA's embedded-command markers (\\x01), like
				# eSpeak does; the frontend would otherwise fail on them.
				cleaned = item.translate({0x1: None})
				textParts.append(cleaned)
				charCount += len(cleaned)
			elif isinstance(item, IndexCommand):
				indexes.append((charCount, item.index))
		text = "".join(textParts)
		if not text.strip() and not indexes:
			return
		cancelEvent = threading.Event()
		with self._speechQueueLock:
			self._speechQueue.append((text, indexes, cancelEvent))
		self._queueNotEmpty.set()

	def _speechLoop(self) -> None:
		"""Speak queued utterances one at a time, in order.

		NVDA can call speak() for the next line before the previous one
		has finished playing (it relies on the synthesizer to serialize),
		so a single worker here guarantees audio never overlaps. The
		worker re-checks its own cancel event between chunks so cancel()
		cuts speech immediately. The tail of each utterance is used to
		pre-synthesize the first piece of the next one (see _prefetchNext),
		so consecutive voice outputs join with only the natural
		punctuation pause.
		"""
		prefetched: Prefetched | None = None
		while not self._stopRequested.is_set():
			self._queueNotEmpty.wait()
			with self._speechQueueLock:
				# Only clear the wake-up flag once the queue is confirmed
				# empty: a speak() that appended between wait() returning
				# and clear() would otherwise lose its wake-up and its
				# utterance would stay stuck in the queue forever.
				if not self._speechQueue:
					self._queueNotEmpty.clear()
					continue
				item = self._speechQueue.popleft()
				text, indexes, cancelEvent = item
				# Atomically claim the current generation so cancel()
				# targets the utterance we are about to speak.
				self._generationCanceled = cancelEvent
			# Only reuse the pre-synthesized audio if it belongs to this
			# exact utterance; cancel() may have cleared and refilled the
			# queue since it was prefetched.
			pieces = (
				prefetched if prefetched is not None and prefetched[0] is cancelEvent else None
			)
			# _speakText already catches every synthesis error, so this handler
			# is normally unreachable; it is kept as a deliberate safety net so
			# that an unexpected bug can never silently kill this daemon
			# worker thread (which would leave the synthesizer mute forever).
			try:
				prefetched = self._speakText(text, indexes, cancelEvent, pieces)
			except Exception:
				log.exception("Inflect Micro TTS: synthesis failed")
				prefetched = None
				synthDoneSpeaking.notify(synth=self)

	def _speakText(
		self,
		text: str,
		indexes: list[tuple[int, int]],
		cancelEvent: threading.Event,
		prefetched: Prefetched | None = None,
	) -> Prefetched | None:
		"""Speak one utterance and pre-synthesize the next one.

		When ``prefetched`` matches this utterance (see _speechLoop) its
		first piece has already been synthesized while the previous
		utterance was playing, so feeding starts without any inference
		latency. Before this utterance's audio has finished playing, the
		first piece of the next queued utterance is synthesized (see
		_prefetchNext) and returned for the loop to hand over.

		:return: The prefetched audio for the next queued utterance, or
			None when there is none.
		"""
		try:
			# Imported lazily so that a problem with the bundled
			# dependencies never prevents the driver from loading.
			from ._inflectMicro.engine import EngineError, getEngine

			if not text.strip():
				# Index-only utterance (e.g. the say-all finish callback):
				# there is no audio, so report every index immediately.
				# Checked before loading the engine so this path works even
				# when the model is missing or broken.
				for _, index in indexes:
					synthIndexReached.notify(synth=self, index=index)
				synthDoneSpeaking.notify(synth=self)
				return None
			engine = getEngine(self.voice)
			player = self._getWavePlayer(engine.sampleRate)
			if player is None or cancelEvent.is_set():
				return None
			piecesIter: Iterator[tuple[bytes, list[int]]]
			if prefetched is not None and prefetched[0] is cancelEvent and not prefetched[0].is_set():
				# The first piece of this utterance was pre-synthesized by
				# the previous utterance; feed it now and stream the rest.
				piecesIter = chain((prefetched[1],), prefetched[2])
			else:
				piecesIter = engine.synthesizeStream(
					text,
					speed=self._speedForRate(),
					variation=self.variation / 100.0,
					seed=self.seed,
					volume=self.volume / 100.0,
					indexes=indexes,
				)
			for chunk, indexesToFire in piecesIter:
				if cancelEvent.is_set():
					break
				# Indexes are reported once their audio chunk has been fed to
				# the player (not after it finishes playing, as oneCore does
				# via onDone). That is intentional: NVDA tolerates slightly
				# early indexes, and our single-worker queue holds any
				# utterance NVDA pushes until player.idle() returns, so the
				# current sentence is never cut short.
				player.feed(chunk)
				for index in indexesToFire:
					synthIndexReached.notify(synth=self, index=index)
			if cancelEvent.is_set():
				return None
			# While this utterance's tail audio is still playing, synthesize
			# the first piece of the next queued utterance so the two voice
			# outputs join with only the natural punctuation pause.
			nextPrefetched = self._prefetchNext()
			if cancelEvent.is_set():
				return None
			player.idle()
			synthDoneSpeaking.notify(synth=self)
			return nextPrefetched
		except EngineError as exc:
			log.error(f"Inflect Micro TTS: {exc}")
			synthDoneSpeaking.notify(synth=self)
			return None
		except Exception:
			log.exception("Inflect Micro TTS: synthesis failed")
			synthDoneSpeaking.notify(synth=self)
			return None

	def _prefetchNext(self) -> Prefetched | None:
		"""Pre-synthesize the first piece of the next queued utterance.

		NVDA pushes the next line of long text (say-all) before the current
		one has finished playing, relying on the synth to serialize. Pulling
		the first piece now - while the current utterance's tail audio is
		still playing and before player.idle() blocks - hides the model's
		inference latency behind that playback, so the two voice outputs
		join with only the natural punctuation pause instead of a dead-air
		gap.

		:return: ``(cancelEvent, firstPiece, restStream)`` for the next
			queued utterance, or None when there is none.
		"""
		with self._speechQueueLock:
			if not self._speechQueue:
				return None
			nextItem = self._speechQueue[0]
			nextText, nextIndexes, nextCancel = nextItem
		if not nextText.strip() or nextCancel.is_set():
			return None
		try:
			from ._inflectMicro.engine import getEngine

			engine = getEngine(self.voice)
			stream = engine.synthesizeStream(
				nextText,
				speed=self._speedForRate(),
				variation=self.variation / 100.0,
				seed=self.seed,
				volume=self.volume / 100.0,
				indexes=nextIndexes,
			)
			firstPiece = next(stream)
		except StopIteration:
			return None
		except Exception:
			# The next utterance is synthesized (and any error reported)
			# when it is actually spoken.
			log.debugWarning(
				"Inflect Micro TTS: could not pre-synthesize the next utterance",
				exc_info=True,
			)
			return None
		if nextCancel.is_set():
			return None
		# The first piece was synthesized with the settings read above; if the
		# user changes rate/volume/variation/seed in the brief window before
		# this utterance is spoken, only its first chunk would carry the old
		# values. NVDA re-speaks on every setting change (cancelling first),
		# so in practice the prefetch is discarded before it can matter.
		return nextCancel, firstPiece, stream

	def cancel(self) -> None:
		with self._speechQueueLock:
			# Drop every utterance that has not started yet.
			for _, _, queuedEvent in self._speechQueue:
				queuedEvent.set()
			self._speechQueue.clear()
			# Abort the utterance the worker is currently speaking.
			self._generationCanceled.set()
		self._queueNotEmpty.set()
		# Guarded by the same lock as _getWavePlayer so a worker that is
		# mid-feed cannot race the stop.
		with self._wavePlayerLock:
			if self._wavePlayer is not None:
				self._wavePlayer.stop()

	def terminate(self) -> None:
		# Signal the worker before tearing the audio down: a worker blocked
		# in feed() must not reopen the stopped player and keep talking.
		self._stopRequested.set()
		self.cancel()
		with self._wavePlayerLock:
			if self._wavePlayer is not None:
				self._wavePlayer.stop()
				self._wavePlayer = None
		# Release the ONNX sessions and any cached model files.
		try:
			from ._inflectMicro.engine import releaseEngine

			releaseEngine()
		except Exception:
			log.debugWarning("Inflect Micro TTS: error while releasing engine", exc_info=True)
		super().terminate()
