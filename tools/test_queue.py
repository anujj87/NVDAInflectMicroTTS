# tools/test_queue.py
"""Standalone regression tests for the driver's serialized speech queue.

NVDA's speech manager pushes several speak() calls back to back while
reading long text (say-all) and relies on the synthesizer to serialize
them. This test stubs the NVDA modules the driver imports and verifies
that:

1. Back-to-back speak() calls never overlap (single worker thread).
2. No utterance is ever lost, even under a tight speak() race (this is
   the lost-wake-up regression from clearing the queue flag too early).
3. cancel() interrupts the current utterance and drops queued ones.
4. cancel() followed by speak() resumes promptly.
5. IndexCommands reach the worker and fire synthIndexReached in order.
6. Index-only utterances (say-all finish callback) still fire.

Usage (from the repository root):

    python tools/test_queue.py
"""

from __future__ import annotations

import builtins
import sys
import threading
import time
import types
from pathlib import Path

# NVDA injects _() into builtins; the driver uses it at class level.
if not hasattr(builtins, "_"):
	builtins._ = lambda text: text

ADDON = Path(__file__).resolve().parents[1] / "addon"
sys.path.insert(0, str(ADDON))

# ---- stub the NVDA modules the driver imports ----

config = types.ModuleType("config")
config.conf = {"audio": {"outputDevice": None}}
sys.modules["config"] = config


class _WavePlayer:
	def __init__(self, channels, samplesPerSec, bitsPerSample, outputDevice):
		self.channels = channels
		self.samplesPerSec = samplesPerSec
		self.bitsPerSample = bitsPerSample
		self.outputDevice = outputDevice
		self.stopped = False
		self.idled = False

	def feed(self, data: bytes) -> None:
		pass

	def stop(self) -> None:
		self.stopped = True

	def idle(self) -> None:
		self.idled = True


nvwave = types.ModuleType("nvwave")
nvwave.WavePlayer = _WavePlayer
sys.modules["nvwave"] = nvwave

driverSetting = types.ModuleType("autoSettingsUtils.driverSetting")
driverSetting.NumericDriverSetting = lambda *args, **kwargs: None
autoSettingsUtils = types.ModuleType("autoSettingsUtils")
autoSettingsUtils.driverSetting = driverSetting
sys.modules["autoSettingsUtils"] = autoSettingsUtils
sys.modules["autoSettingsUtils.driverSetting"] = driverSetting

logHandler = types.ModuleType("logHandler")
logHandler.log = types.SimpleNamespace(
	debug=lambda *a, **k: None,
	debugWarning=lambda *a, **k: None,
	error=lambda *a, **k: None,
	exception=lambda *a, **k: None,
)
sys.modules["logHandler"] = logHandler

speechTypes = types.ModuleType("speech.types")
speechTypes.SpeechSequence = list
speechCommands = types.ModuleType("speech.commands")


class IndexCommand:
	def __init__(self, index):
		self.index = index


speechCommands.IndexCommand = IndexCommand
speech = types.ModuleType("speech")
speech.types = speechTypes
speech.commands = speechCommands
sys.modules["speech"] = speech
sys.modules["speech.types"] = speechTypes
sys.modules["speech.commands"] = speechCommands

synthDriverHandler = types.ModuleType("synthDriverHandler")


class VoiceInfo:
	def __init__(self, id, displayName, language=None):
		self.id = id
		self.displayName = displayName
		self.language = language


class SynthDriver:
	name = ""
	description = ""
	supportedSettings = ()
	supportedNotifications = set()

	@classmethod
	def VoiceSetting(cls):
		return ("voice",)

	@classmethod
	def RateSetting(cls):
		return ("rate",)

	@classmethod
	def RateBoostSetting(cls):
		return ("rateBoost",)

	def __init__(self):
		pass

	def terminate(self):
		pass


synthDriverHandler.SynthDriver = SynthDriver
synthDriverHandler.VoiceInfo = VoiceInfo


class _Notification:
	"""Hashable stand-in for a real notification object (the driver
	stores them in a set); records what was notified."""

	def __init__(self):
		self.calls: list[dict] = []

	def notify(self, **kwargs) -> None:
		self.calls.append(kwargs)


synthDriverHandler.synthDoneSpeaking = _Notification()
synthDriverHandler.synthIndexReached = _Notification()
sys.modules["synthDriverHandler"] = synthDriverHandler

# ---- import the real driver ----

from synthDrivers.inflectMicroTTS import SynthDriver as Driver  # noqa: E402

# The preload would load the real ONNX model; keep the test hermetic.
Driver._preloadEngine = lambda self: None  # type: ignore[method-assign]

failures: list[str] = []


def check(condition: bool, message: str) -> None:
	if condition:
		print(f"  ok: {message}")
	else:
		failures.append(message)
		print(f"  FAIL: {message}")


def runSerializationTest() -> None:
	print("Test 1: back-to-back speak() calls never overlap")
	state = {"active": 0, "maxActive": 0, "spoken": [], "lock": threading.Lock()}

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		with state["lock"]:
			state["active"] += 1
			state["maxActive"] = max(state["maxActive"], state["active"])
			state["spoken"].append(text)
		try:
			time.sleep(0.05)
		finally:
			with state["lock"]:
				state["active"] -= 1

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		# Simulate say-all: many speak() calls with no cancel between them.
		texts = [f"utterance {i}" for i in range(5)]
		for text in texts:
			driver.speak([text])
		deadline = time.monotonic() + 10
		while len(state["spoken"]) < len(texts) and time.monotonic() < deadline:
			time.sleep(0.005)
		check(state["spoken"] == texts, f"all utterances spoken in order (got {state['spoken']})")
		check(state["maxActive"] == 1, f"no overlap; max concurrent == 1 (got {state['maxActive']})")
	finally:
		driver.terminate()


def runRaceTest() -> None:
	print("Test 2: no utterance is lost under a tight speak() race")
	state = {"spoken": [], "lock": threading.Lock()}

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		time.sleep(0.01)
		with state["lock"]:
			state["spoken"].append(text)

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		texts = [f"race {i}" for i in range(30)]
		for text in texts:
			driver.speak([text])
			time.sleep(0.0005)  # force the worker to wake mid-append
		deadline = time.monotonic() + 10
		while len(state["spoken"]) < len(texts) and time.monotonic() < deadline:
			time.sleep(0.005)
		check(
			len(state["spoken"]) == len(texts),
			f"all {len(texts)} utterances spoken (got {len(state['spoken'])})",
		)
	finally:
		driver.terminate()


def runCancelTest() -> None:
	print("Test 3: cancel() interrupts the active utterance and drops queued ones")
	state = {"started": [], "interrupted": [], "completed": [], "lock": threading.Lock()}

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		with state["lock"]:
			state["started"].append(text)
		for _ in range(100):
			if cancelEvent.is_set():
				with state["lock"]:
					state["interrupted"].append(text)
				return
			time.sleep(0.005)
		with state["lock"]:
			state["completed"].append(text)

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		driver.speak(["long"])
		deadline = time.monotonic() + 5
		while "long" not in state["started"] and time.monotonic() < deadline:
			time.sleep(0.005)
		driver.speak(["queued1"])
		driver.speak(["queued2"])
		time.sleep(0.02)
		driver.cancel()
		deadline = time.monotonic() + 5
		while not state["interrupted"] and time.monotonic() < deadline:
			time.sleep(0.005)
		check(state["interrupted"] == ["long"], f"active utterance interrupted (got {state['interrupted']})")
		check(
			"queued1" not in state["started"] and "queued2" not in state["started"],
			"queued utterances dropped",
		)
		check("long" not in state["completed"], "interrupted utterance did not complete")
	finally:
		driver.terminate()


def runResumeTest() -> None:
	print("Test 4: speech resumes promptly after cancel()")
	state = {"started": [], "completed": [], "lock": threading.Lock()}

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		if cancelEvent.is_set():
			return
		with state["lock"]:
			state["started"].append(text)
		# Long enough that cancel() below interrupts it mid-way.
		deadline = time.monotonic() + 0.5
		while time.monotonic() < deadline:
			if cancelEvent.is_set():
				return
			time.sleep(0.005)
		with state["lock"]:
			state["completed"].append(text)

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		driver.speak(["first"])
		# Wait until the worker is actually mid-utterance before cancelling.
		deadline = time.monotonic() + 5
		while "first" not in state["started"] and time.monotonic() < deadline:
			time.sleep(0.005)
		driver.cancel()
		start = time.monotonic()
		driver.speak(["second"])
		deadline = time.monotonic() + 5
		while "second" not in state["completed"] and time.monotonic() < deadline:
			time.sleep(0.005)
		elapsed = time.monotonic() - start
		check("first" not in state["completed"], "cancelled utterance did not complete")
		check("second" in state["completed"], "next utterance spoken after cancel()")
		check(elapsed < 1.0, f"next utterance started promptly ({elapsed:.3f}s)")
	finally:
		driver.terminate()


def runEnginePacingTest() -> None:
	print("Test 5: engine boundary pauses are sentence-aware")
	try:
		# Imported as part of the synthDrivers package (the engine is not
		# a top-level module; test_synth.py instead adds the synthDrivers
		# dir to sys.path).
		from synthDrivers._inflectMicro.engine import (
			boundaryPauseSeconds,
			trailingSilenceSeconds,
		)
	except Exception as exc:  # noqa: BLE001
		check(False, f"engine importable for pacing test ({exc})")
		return
	check(boundaryPauseSeconds("Hello world.") == 0.28, "period -> 0.28s")
	check(boundaryPauseSeconds("Really?") == 0.32, "question -> 0.32s")
	check(boundaryPauseSeconds("Wow!") == 0.28, "exclamation -> 0.28s")
	check(boundaryPauseSeconds("One; two;") == 0.18, "semicolon -> 0.18s")
	check(boundaryPauseSeconds("plain") == 0.10, "no punctuation -> short pause")
	check(
		trailingSilenceSeconds("Hello world.") == 0.28,
		"trailing pause after period matches sentence pause",
	)
	check(
		trailingSilenceSeconds("plain") == 0.15,
		"trailing pause floor keeps a safety margin",
	)
	check(
		trailingSilenceSeconds("continues onto the next line") == 0.15,
		"mid-sentence line break gets a short continuation pause",
	)
	check(
		trailingSilenceSeconds("wait,") == 0.15,
		"line ending in a comma gets a short continuation pause",
	)
	check(
		trailingSilenceSeconds('She said "hello."') == 0.28,
		"sentence wrapped in closing quotes still gets the full pause",
	)


def runIndexTest() -> None:
	print("Test 6: IndexCommands reach the worker and fire synthIndexReached")
	state = {"spoken": [], "lock": threading.Lock()}
	indexCalls: list[dict] = []
	synthDriverHandler.synthIndexReached.calls = indexCalls
	synthDriverHandler.synthDoneSpeaking.calls = []

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		with state["lock"]:
			state["spoken"].append((text, indexes))
		# Simulate the engine reporting indexes as it streams.
		for _, index in indexes:
			synthDriverHandler.synthIndexReached.notify(synth=self, index=index)

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		driver.speak(["Hello", IndexCommand(7), " world", IndexCommand(8)])
		deadline = time.monotonic() + 5
		while not state["spoken"] and time.monotonic() < deadline:
			time.sleep(0.005)
		check(len(state["spoken"]) == 1, "utterance spoken")
		text, indexes = state["spoken"][0]
		check(text == "Hello world", f"IndexCommands stripped from text (got {text!r})")
		check(indexes == [(5, 7), (11, 8)], f"offsets collected (got {indexes})")
		check(
			[indexCall.get("index") for indexCall in indexCalls] == [7, 8],
			f"synthIndexReached fired in order (got {indexCalls})",
		)
	finally:
		driver.terminate()


def runIndexOnlyTest() -> None:
	print("Test 7: index-only utterance (say-all finish callback) still fires")
	indexCalls: list[dict] = []
	synthDriverHandler.synthIndexReached.calls = indexCalls
	synthDriverHandler.synthDoneSpeaking.calls = []

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		for _, index in indexes:
			synthDriverHandler.synthIndexReached.notify(synth=self, index=index)

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		driver.speak([IndexCommand(3)])
		deadline = time.monotonic() + 5
		while not indexCalls and time.monotonic() < deadline:
			time.sleep(0.005)
		check(
			[indexCall.get("index") for indexCall in indexCalls] == [3],
			f"index-only utterance fired (got {indexCalls})",
		)
	finally:
		driver.terminate()


def runPrefetchTest() -> None:
	print("Test 8: next utterance is pre-synthesized while the current one plays")
	state = {"calls": [], "lock": threading.Lock()}

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		with state["lock"]:
			state["calls"].append((text, prefetched))
		if text == "first":
			# Wait until the second utterance is queued, then pretend to
			# pre-synthesize its first piece (as _prefetchNext does).
			deadline = time.monotonic() + 5
			while time.monotonic() < deadline:
				with self._speechQueueLock:
					if self._speechQueue:
						nextItem = self._speechQueue[0]
						return (
							nextItem[2],
							(b"prefetched-first", []),
							iter([(b"rest-piece", [])]),
						)
				time.sleep(0.005)
		return None

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		driver.speak(["first"])
		driver.speak(["second"])
		deadline = time.monotonic() + 5
		while len(state["calls"]) < 2 and time.monotonic() < deadline:
			time.sleep(0.005)
		check(len(state["calls"]) == 2, "both utterances spoken")
		secondCall = state["calls"][1]
		check(
			secondCall[0] == "second" and secondCall[1] is not None,
			f"prefetched audio handed to next utterance (got {secondCall})",
		)
	finally:
		driver.terminate()


def runPrefetchDiscardTest() -> None:
	print("Test 9: stale pre-synthesized audio is discarded after cancel()")
	state = {"calls": [], "lock": threading.Lock()}
	prefetchReturned = threading.Event()

	def fakeSpeakText(self, text, indexes, cancelEvent, prefetched=None):
		with state["lock"]:
			state["calls"].append((text, prefetched))
		if text == "first":
			# Wait until the second utterance is queued, then "pre-synthesize"
			# it so the loop holds a prefetch for an item about to be dropped.
			deadline = time.monotonic() + 5
			while time.monotonic() < deadline:
				with self._speechQueueLock:
					if self._speechQueue:
						nextItem = self._speechQueue[0]
						prefetchReturned.set()
						return (
							nextItem[2],
							(b"prefetched-first", []),
							iter([(b"rest-piece", [])]),
						)
				time.sleep(0.005)
		return None

	Driver._speakText = fakeSpeakText  # type: ignore[method-assign]
	driver = Driver()
	try:
		driver.speak(["first"])
		driver.speak(["second"])
		# Let the worker produce the prefetch, then cancel so the queue is
		# cleared and the prefetched item is gone.
		deadline = time.monotonic() + 5
		while not prefetchReturned.is_set() and time.monotonic() < deadline:
			time.sleep(0.005)
		driver.cancel()
		driver.speak(["third"])
		deadline = time.monotonic() + 5
		while len(state["calls"]) < 3 and time.monotonic() < deadline:
			time.sleep(0.005)
		check(len(state["calls"]) == 3, "all three utterances spoken")
		thirdCall = state["calls"][2]
		check(
			thirdCall[0] == "third" and thirdCall[1] is None,
			f"stale prefetch not handed to utterance after cancel (got {thirdCall})",
		)
	finally:
		driver.terminate()


if __name__ == "__main__":
	runSerializationTest()
	runRaceTest()
	runCancelTest()
	runResumeTest()
	runEnginePacingTest()
	runIndexTest()
	runIndexOnlyTest()
	runPrefetchTest()
	runPrefetchDiscardTest()
	print()
	if failures:
		print(f"{len(failures)} FAILURE(S):")
		for failure in failures:
			print(f"  - {failure}")
		raise SystemExit(1)
	print("All queue tests passed.")
