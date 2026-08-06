# numpy/onnxruntime are vendored in lib/ at runtime, so pyright cannot see
# them; the Unknown-type rules are therefore disabled for this file.
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
# addon/synthDrivers/_inflectMicro/engine.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""In-process ONNX Runtime engine for the Inflect Micro v2 model.

The text frontend (normalization + phonemization) and the pre/post
processing below mirror the model's official inference code; only the
model forward pass runs through ONNX Runtime instead of PyTorch.

Adapted (Apache-2.0) from:
* robertbak/ha-inflect-tts - custom_components/inflect_tts/onnx_engine.py
* owensong/Inflect-Micro-v2-ONNX - onnx/inference_onnx.py,
  inflect_vits_frontend.py and inflect_nano_v2_frontend.py

The driver imports this module lazily (on the first utterance), so a
missing or broken vendored ``lib`` folder degrades gracefully: NVDA still
lists the driver and only logs an error when speech is attempted.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import re
import sys
import threading
import time
import types
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np  # pyright: ignore[reportMissingImports]  # vendored in lib/
import onnxruntime as ort  # pyright: ignore[reportMissingImports]  # vendored in lib/

from . import providers
from .provision import BUNDLED_VOICE_ID, EngineError, findModelDir

#: Smallest pause appended after the true end of a synthesized message so
#: that no trailing sounds get clipped by the audio pipeline.
_MIN_TRAILING_SILENCE_SECONDS = 0.15

#: Serializes text-frontend (eSpeak) access. espeak-ng is not thread-safe
#: and the cached phonemizer backend is shared between the preload and the
#: speak worker threads.
_frontendLock = threading.Lock()

_logger = logging.getLogger("inflectMicroTTS")


def _stubOutUnusedSegmentsBackend() -> None:
	"""Allow phonemizer to be imported without the ``segments`` package.

	phonemizer eagerly imports its SegmentsBackend, which pulls in a
	large dependency tree. Only the eSpeak backend is used here, so the
	unused module is stubbed out (same trick as ha-inflect-tts).
	"""
	if "segments" not in sys.modules:
		stub = types.ModuleType("segments")
		setattr(stub, "Tokenizer", object)
		setattr(stub, "Profile", object)
		setattr(stub, "__version__", "0.0.0-stub")
		sys.modules["segments"] = stub


def splitText(text: str, limit: int = 280, firstLimit: int = 60) -> list[str]:
	"""Split text into sentence-sized chunks for synthesis.

	The first chunk is additionally capped at ``firstLimit`` characters so
	the first decode pass (and therefore the first spoken word) finishes
	quickly even for long labels; the remaining chunks keep the natural
	sentence boundaries up to ``limit``.
	"""
	normalized = " ".join(text.split())
	sentences = [part.strip() for part in re.split(r"(?<=[.!?;:])\s+", normalized) if part.strip()]
	chunks: list[str] = []
	isFirst = True
	for sentence in sentences or [normalized]:
		while len(sentence) > (firstLimit if isFirst else limit):
			effective = firstLimit if isFirst else limit
			search = sentence[: effective + 1]
			punctuation = max(search.rfind(mark) for mark in (",", ";", ":"))
			splitAt = (
				punctuation + 1 if punctuation >= effective // 2 else sentence.rfind(" ", 0, effective + 1)
			)
			if splitAt < effective // 2:
				splitAt = effective
			chunks.append(sentence[:splitAt].strip())
			sentence = sentence[splitAt:].strip()
			isFirst = False
		if sentence:
			chunks.append(sentence)
			isFirst = False
	return chunks


def boundaryPauseSeconds(chunk: str) -> float:
	"""Natural pause length after a chunk, based on its final punctuation."""
	ending = chunk.rstrip()[-1:] if chunk.strip() else ""
	return {
		"?": 0.32,
		"!": 0.28,
		".": 0.28,
		";": 0.18,
		":": 0.15,
		",": 0.10,
	}.get(ending, 0.10)


#: Characters that may wrap a sentence's final punctuation (e.g. the
#: closing quote in ``She said "hello."``); the trailing pause looks past
#: them at the punctuation they wrap.
_TRAILING_WRAPPERS = "\"')”’]}>"


def trailingSilenceSeconds(endingText: str) -> float:
	"""Natural pause after a finished utterance, based on its punctuation.

	Uses the same punctuation-driven rhythm as the pauses between chunks
	inside one utterance, so two consecutive utterances join with the same
	rhythm as two sentences of one utterance. Pass the original utterance
	text (not a prosody-normalized chunk): say-all sends text line by line
	(or paragraph by paragraph), and a line that breaks mid-sentence must
	not be padded to a full sentence stop just because the engine appended
	a closing period. A small floor keeps enough leeway for the audio
	pipeline so the final sounds are never clipped.
	"""
	# ``She said "hello."`` really ends with a sentence stop; look past the
	# closing wrapper characters to find the punctuation they wrap.
	ending = endingText.rstrip().rstrip(_TRAILING_WRAPPERS)
	return max(boundaryPauseSeconds(ending), _MIN_TRAILING_SILENCE_SECONDS)


def chunkIndexesToFire(
	chunks: list[str],
	indexes: list[tuple[int, int]],
	originalLength: int,
) -> tuple[list[list[int]], list[int]]:
	"""Map ``(charOffset, index)`` pairs onto synthesized sentence chunks.

	NVDA marks points of interest in an utterance with ``IndexCommand`` s
	(for example the end of the utterance, or sayAll's ``lineReached``
	callback) and expects the synthesizer to fire ``synthIndexReached`` for
	each one as the audio passes it. Offsets are given in the original text;
	the chunks are whitespace-normalized, so offsets are mapped
	proportionally to the chunks' total length, which keeps every index on
	the correct chunk without needing a lossless normalizer.

	:return: ``(perChunk, remaining)`` where ``perChunk[i]`` is the list of
		indexes to fire after chunk ``i`` has been fed, and ``remaining``
		are the indexes beyond the last chunk (e.g. an end-of-utterance
		index placed exactly at the end of the text) which the caller
		should fire with the trailing silence piece.
	"""
	perChunk: list[list[int]] = [[] for _ in chunks]
	if not indexes or not chunks or originalLength <= 0:
		return perChunk, [index for _, index in indexes]
	totalNormalized = sum(len(chunk) for chunk in chunks)
	if totalNormalized <= 0:
		return perChunk, [index for _, index in indexes]
	pointer = 0
	cumulative = 0
	for chunkIndex, chunk in enumerate(chunks):
		cumulative += len(chunk)
		fired: list[int] = []
		while pointer < len(indexes):
			offset, index = indexes[pointer]
			normalizedOffset = offset * totalNormalized / originalLength
			if normalizedOffset > cumulative:
				break
			fired.append(index)
			pointer += 1
		perChunk[chunkIndex] = fired
	remaining = [index for _, index in indexes[pointer:]]
	return perChunk, remaining


def edgeFade(waveform: np.ndarray, sampleRate: int, milliseconds: float = 5.0) -> np.ndarray:
	"""Apply a short fade in/out to avoid clicks at chunk boundaries."""
	frames = min(round(sampleRate * milliseconds / 1000.0), waveform.size // 2)
	if frames <= 0:
		return waveform
	output = waveform.copy()
	ramp = np.linspace(0.0, 1.0, frames, endpoint=True, dtype=np.float32)
	output[:frames] *= ramp
	output[-frames:] *= ramp[::-1]
	return output


def _intersperse(seq: list[int], item: int) -> list[int]:
	result = [item] * (len(seq) * 2 + 1)
	result[1::2] = seq
	return result


def _installCachedPhonemizer(frontend: types.ModuleType) -> None:
	"""Reuse one eSpeak backend instead of phonemizer's per-call construction.

	phonemizer's top-level ``phonemize()`` builds a fresh ``EspeakBackend``
	on every call, and espeak_Initialize alone costs ~140 ms per chunk. The
	engine always phonemizes with a single job, so a single cached backend
	using the exact parameters the vits frontend passes to ``phonemize()``
	produces byte-identical output in well under a millisecond.
	"""
	original = frontend.phonemize_normalized_batch
	state: dict[str, Any] = {"backend": None, "separator": None}

	def phonemize_normalized_batch(normalized_texts: list[str], *, jobs: int = 1) -> list[str]:
		texts = [text for text in normalized_texts if text.strip()]
		if not texts:
			return []
		if jobs != 1:
			# Multi-job phonemization is not used by the engine; keep the
			# original behavior available (it configures espeak itself).
			return original(normalized_texts, jobs=jobs)
		if state["backend"] is None:
			# Same as the original frontend path: point espeakng_loader at
			# the bundled DLL/data before constructing the backend.
			frontend._configure_espeak()  # pyright: ignore[reportPrivateUsage]
			from phonemizer.backend import EspeakBackend
			from phonemizer.separator import default_separator

			state["backend"] = EspeakBackend(language="en-us", preserve_punctuation=True, with_stress=True)
			state["separator"] = default_separator
		return state["backend"].phonemize(texts, separator=state["separator"], strip=True, njobs=1)

	frontend.phonemize_normalized_batch = phonemize_normalized_batch


def _importFrontend(artifactDir: Path) -> types.ModuleType:
	"""Load the model's own text frontend from the model directory."""
	runtimeRoot = artifactDir / "runtime"
	for path in (str(runtimeRoot), str(artifactDir)):
		if path not in sys.path:
			sys.path.insert(0, path)
	spec = importlib.util.spec_from_file_location(
		"inflect_onnx_frontend", str(artifactDir / "inflect_vits_frontend.py")
	)
	if spec is None or spec.loader is None:
		raise EngineError(f"Could not load the text frontend from {artifactDir}")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


def _sessionOptions(provider: str) -> ort.SessionOptions:
	"""Tuned ONNX Runtime session options for the given provider.

	These are small graphs. 4 intra-op threads cut decode latency by
	roughly a third compared to 2 (8 oversubscribes and is slower
	again), which directly shortens the gap between an NVDA action and
	the first spoken word. Cap at the core count so a small machine is
	not oversubscribed. Keeping the CPU memory arena and memory pattern
	enabled lets onnxruntime reuse its intermediate buffers between
	utterances, which measurably speeds up each decode pass. Graph
	optimization stays at the extended level for CPU (matching previous
	behaviour); DirectML benefits from the standard optimizations, so
	they are raised to full for GPU.
	"""
	options = ort.SessionOptions()
	options.intra_op_num_threads = min(4, os.cpu_count() or 4)
	options.inter_op_num_threads = 1
	options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
	options.enable_cpu_mem_arena = True
	options.enable_mem_pattern = True
	if provider == providers.CPU:
		options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED
	else:
		options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
	return options


#: The provider the cached engine(s) actually run on. When the configured
#: provider fails to load (e.g. DirectML without a usable DX12 device) the
#: engine falls back to CPU and the fallback is remembered for the rest of
#: the NVDA session, so later loads do not retry the failing provider. The
#: user's configured choice is NOT rewritten - the settings panel shows the
#: honest (probed) availability instead. Guarded by _engineLock.
_sessionProviderFallback: str | None = None


def _effectiveProvider() -> str:
	"""The provider engines should be loaded with right now."""
	global _sessionProviderFallback
	if _sessionProviderFallback is not None:
		return _sessionProviderFallback
	return providers.getProvider()


def _rememberFallback() -> None:
	"""Remember that the configured provider failed and CPU is in use."""
	global _sessionProviderFallback
	_sessionProviderFallback = providers.CPU


class OnnxInflectEngine:
	"""Loads and runs the two-graph ONNX export of the Inflect model."""

	def __init__(self, artifactDir: Path) -> None:
		super().__init__()
		self._artifactDir = artifactDir
		self._durationSess: Any = None
		self._decodeSess: Any = None
		self._frontend: Any = None
		self._sampleRate = 24_000
		self._addBlank = True
		#: The execution provider this engine's sessions were created
		#: with (set by load(); used to detect provider setting changes).
		self.provider: str = providers.CPU
		#: Serializes inference on this engine's ONNX sessions. The
		#: driver's background preload (warmUp) and the speech worker can
		#: otherwise run the sessions concurrently, which crashes the
		#: process with a native access violation when the DirectML (GPU)
		#: provider is active - ONNX Runtime only guarantees thread-safe
		#: concurrent ``run()`` calls for the CPU provider. One engine has
		#: one voice and the driver speaks utterances one at a time, so
		#: serializing costs nothing in practice.
		self._runLock = threading.Lock()

	@property
	def sampleRate(self) -> int:
		return self._sampleRate

	def warmUp(self) -> None:
		"""Run the one-time expensive initializations without producing audio.

		Initializes the cached eSpeak backend and the text pipeline and runs
		one tiny inference through both ONNX graphs, so the first real
		utterance starts speaking immediately instead of paying the ~0.5 s
		cold start (including the ONNX thread-pool spin-up). Callers are
		expected to guard against errors (e.g. the background preload in the
		driver).
		"""
		if self._durationSess is None or self._decodeSess is None:
			return
		# _runChunk runs the frontend plus both ONNX graphs and discards the
		# tiny "w" waveform, which is exactly the warm-up we want.
		self._runChunk("w", 1.0, 0.667, np.random.RandomState(0))

	def load(self) -> None:
		"""Load the ONNX sessions and the text frontend. Blocking.

		The execution provider is resolved from the add-on's settings
		("auto" picks the best usable provider). If the chosen
		accelerator cannot run the model on this machine, the engine
		falls back to CPU; the fallback is remembered for the rest of the
		NVDA session so later loads do not retry the failing provider.
		"""
		_stubOutUnusedSegmentsBackend()
		startTime = time.monotonic()
		try:
			config = json.loads((self._artifactDir / "config.json").read_text(encoding="utf-8"))
			self._sampleRate = int(config["data"]["sampling_rate"])
			self._addBlank = bool(config["data"]["add_blank"])
			self._frontend = _importFrontend(self._artifactDir)
			_installCachedPhonemizer(self._frontend)
		except Exception as exc:
			raise EngineError(f"Failed to load the Inflect Micro v2 model: {exc}") from exc
		provider = _effectiveProvider()
		try:
			self._createSessions(provider)
		except Exception as exc:
			if provider == providers.CPU:
				raise EngineError(f"Failed to load the Inflect Micro v2 model: {exc}") from exc
			# The chosen accelerator could not run the model on this
			# machine (e.g. DirectML without a usable DX12 device). Fall
			# back to CPU so speech keeps working; remember the fallback
			# for the rest of the session so later loads do not retry the
			# failing provider on every utterance.
			failedProvider = provider
			_rememberFallback()
			provider = providers.CPU
			try:
				self._createSessions(provider)
			except Exception as cpuExc:
				raise EngineError(f"Failed to load the Inflect Micro v2 model: {cpuExc}") from cpuExc
			_logger.warning(
				"Inflect Micro TTS: provider %s failed to load the model; falling back to CPU (%s)",
				failedProvider,
				exc,
			)
		self.provider = provider
		_logger.debug(
			"Model loaded in %.2fs using provider %s",
			time.monotonic() - startTime,
			provider,
		)

	def _createSessions(self, provider: str) -> None:
		"""Create both ONNX sessions with the given provider. Blocking."""
		options = _sessionOptions(provider)
		ortProviders = providers.ortProvidersFor(provider)
		self._durationSess = ort.InferenceSession(
			str(self._artifactDir / "onnx" / "duration.onnx"),
			sess_options=options,
			providers=ortProviders,
		)
		self._decodeSess = ort.InferenceSession(
			str(self._artifactDir / "onnx" / "decode.onnx"),
			sess_options=options,
			providers=ortProviders,
		)
		if provider != providers.CPU:
			# DirectML can silently skip the requested provider when no
			# usable DX12 device exists; only sessions that actually
			# adopted it count as GPU-accelerated.
			if "DmlExecutionProvider" not in self._durationSess.get_providers() or (
				"DmlExecutionProvider" not in self._decodeSess.get_providers()
			):
				raise EngineError("The DirectML provider was not adopted by the ONNX session.")

	def _tokens(self, text: str) -> np.ndarray:
		"""Convert text to the integer token sequence for the model."""
		# Imported from the model's runtime/ folder, which is only placed on
		# sys.path by load()/_importFrontend, so this stays a runtime import.
		# espeak-ng is not thread-safe, so frontend work is serialized.
		with _frontendLock:
			from text import cleaned_text_to_sequence  # pyright: ignore[reportMissingImports]

			phonemes = self._frontend.run_vits_frontend(text).phoneme_text
			sequence = cleaned_text_to_sequence(phonemes)
			if self._addBlank:
				sequence = _intersperse(sequence, 0)
		if not sequence:
			raise EngineError("The text frontend produced no speakable tokens.")
		return np.asarray([sequence], dtype=np.int64)

	def _prepare(self, text: str) -> tuple[str, list[str]]:
		normalized = " ".join(text.split())
		if not normalized:
			raise EngineError("Text must not be empty.")
		if normalized[-1] not in ".!?;:":
			# Without a closing punctuation cue the duration predictor
			# under-allocates frames and the final word sounds clipped.
			normalized += "."
		return normalized, splitText(normalized)

	def _runChunk(self, chunk: str, speed: float, variation: float, rng: np.random.RandomState) -> np.ndarray:
		"""Run one sentence chunk through both ONNX graphs.

		The two ``run()`` calls are serialized with ``_runLock``: the
		driver's preload thread warms the engine up while the speech
		worker may already be synthesizing, and the DirectML (GPU)
		provider crashes with a native access violation when the same
		sessions are run from two threads at once (the CPU provider is
		thread-safe, DirectML is not). Serializing makes the two never
		overlap. The text frontend is not covered by this lock - espeak-ng
		has its own (``_frontendLock``) and it is not the crash source.
		"""
		tokens = self._tokens(chunk)
		lengths = np.asarray([tokens.shape[1]], dtype=np.int64)
		lengthScale = np.asarray(1.0 / speed, dtype=np.float32)
		with self._runLock:
			mPExp, logsPExp, yMask = self._durationSess.run(
				None,
				{"tokens": tokens, "lengths": lengths, "length_scale": lengthScale},
			)
			zpNoise = rng.standard_normal(mPExp.shape).astype(np.float32)
			noiseScale = np.asarray(variation, dtype=np.float32)
			(waveform,) = self._decodeSess.run(
				None,
				{
					"m_p_exp": mPExp,
					"logs_p_exp": logsPExp,
					"y_mask": yMask,
					"zp_noise": zpNoise,
					"noise_scale": noiseScale,
				},
			)
		return edgeFade(waveform[0, 0].astype(np.float32), self._sampleRate)

	def _pausePiece(self, previousChunk: str) -> np.ndarray:
		return np.zeros(
			round(self._sampleRate * boundaryPauseSeconds(previousChunk)),
			dtype=np.float32,
		)

	def _trailingSilencePiece(self, endingText: str) -> np.ndarray:
		return np.zeros(
			round(self._sampleRate * trailingSilenceSeconds(endingText)),
			dtype=np.float32,
		)

	@staticmethod
	def _pcm16Bytes(piece: np.ndarray, volume: float) -> bytes:
		scaled = np.clip(piece * volume, -1.0, 1.0)
		return (scaled * 32767.0).astype("<i2").tobytes()

	def synthesizeStream(
		self,
		text: str,
		*,
		speed: float = 1.0,
		variation: float = 0.667,
		seed: int = 7,
		volume: float = 1.0,
		indexes: list[tuple[int, int]] | None = None,
	) -> Iterator[tuple[bytes, list[int]]]:
		"""Yield ``(pcmBytes, indexesToFire)`` chunk by chunk as they are generated.

		``indexesToFire`` lists the NVDA ``IndexCommand`` numbers (given as
		``(charOffset, index)`` pairs in ``indexes``) whose position in the
		text has been passed by this chunk, so the caller can report them via
		``synthIndexReached`` at exactly the right moment in the audio.

		Blocking: each next() call runs model inference, so callers must
		pull this from a worker thread.
		"""
		if self._durationSess is None or self._decodeSess is None:
			raise EngineError("The Inflect Micro v2 model is not loaded.")
		speed = float(speed)
		variation = float(variation)
		seed = int(seed)
		volume = max(0.0, min(1.0, float(volume)))
		_, chunks = self._prepare(text)
		perChunk, remaining = chunkIndexesToFire(chunks, indexes or [], len(text))
		rng = np.random.RandomState(seed)
		try:
			for index, chunk in enumerate(chunks):
				if index:
					yield self._pcm16Bytes(self._pausePiece(chunks[index - 1]), volume), []
				yield self._pcm16Bytes(self._runChunk(chunk, speed, variation, rng), volume), perChunk[index]
			# The end-of-utterance index (and any other index beyond the last
			# chunk) is reported with the trailing silence, i.e. only once the
			# whole sentence has actually been spoken.
			#
			# The pause is based on the ORIGINAL text's final character, not
			# the last chunk: _prepare() appends a closing period to text
			# lacking one (so the duration predictor does not clip the final
			# word), and using that appended period would turn every mid-
			# sentence line break in say-all into a full sentence stop.
			yield self._pcm16Bytes(self._trailingSilencePiece(text), volume), remaining
		except EngineError:
			raise
		except Exception as exc:
			raise EngineError(f"Synthesis failed: {exc}") from exc

	def synthesize(
		self,
		text: str,
		*,
		speed: float = 1.0,
		variation: float = 0.667,
		seed: int = 7,
		volume: float = 1.0,
	) -> tuple[int, bytes]:
		"""Synthesize the whole text and return (sample rate, WAV bytes)."""
		pieces = list(self.synthesizeStream(text, speed=speed, variation=variation, seed=seed, volume=volume))
		pcm16 = b"".join(pcm for pcm, _ in pieces)
		buffer = io.BytesIO()
		with wave.open(buffer, "wb") as wavFile:
			wavFile.setnchannels(1)
			wavFile.setsampwidth(2)
			wavFile.setframerate(self._sampleRate)
			wavFile.writeframes(pcm16)
		return self._sampleRate, buffer.getvalue()


#: Cached engine instances keyed by voice id, shared by all driver
#: instances. Each voice is a complete model directory with its own ONNX
#: sessions, so switching voices keeps every already-loaded voice warm.
_engines: dict[str, OnnxInflectEngine] = {}
#: The provider each cached engine was loaded with, keyed by voice id.
#: A change of the compute device setting reloads the voice on the new
#: provider. Guarded by _engineLock.
_engineProviders: dict[str, str] = {}
_engineLock = threading.Lock()


def getEngine(voiceId: str | None = None) -> OnnxInflectEngine:
	"""Return the cached engine for a voice, loading it on first use. Blocking.

	Engines are cached per voice and per execution provider: changing the
	compute device in the add-on settings discards the cached sessions so
	the next utterance loads with the new provider.
	"""
	if voiceId is None:
		voiceId = BUNDLED_VOICE_ID
	with _engineLock:
		engine = _engines.get(voiceId)
		if engine is not None and _engineProviders.get(voiceId) != _effectiveProvider():
			# The compute device setting changed (or the previous load fell
			# back to CPU and the configured provider is now usable again);
			# drop the cached sessions so they reload on the new provider.
			_engines.pop(voiceId, None)
			engine = None
		if engine is None:
			engine = OnnxInflectEngine(findModelDir(voiceId))
			engine.load()
			_engines[voiceId] = engine
			_engineProviders[voiceId] = engine.provider
		return engine


def releaseEngine(voiceId: str | None = None) -> None:
	"""Drop the cached engine(s) so their ONNX sessions can be garbage collected."""
	with _engineLock:
		if voiceId is None:
			_engines.clear()
			_engineProviders.clear()
		else:
			_engines.pop(voiceId, None)
			_engineProviders.pop(voiceId, None)
