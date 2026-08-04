# addon/synthDrivers/_inflectMicro/voices.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Catalog and downloader for additional Inflect TTS voices.

The add-on ships one voice (Inflect Micro v2) inside the package. More
voices of the same Inflect TTS family can be downloaded from the add-on
settings panel into a per-user models folder; each voice is a complete
model directory with exactly the same layout as the bundled one (see
:data:`provision.MODEL_FILES`).
"""

from __future__ import annotations

import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .provision import BUNDLED_VOICE_ID, MODEL_FILES, EngineError, isComplete, userModelDir

#: How long an availability result stays cached before the network is
#: consulted again (seconds).
AVAILABILITY_CACHE_SECONDS = 600.0

#: Sent with every request; Hugging Face rejects the default urllib user
#: agent for its download endpoints.
_USER_AGENT = "InflectMicroTTS-addon/0.1"

#: Timeout for a single HTTP request, in seconds.
_REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class VoiceSpec:
	"""A downloadable voice and where to get it from."""

	id: str
	displayName: str
	language: str
	#: Hugging Face repository id, e.g. ``owensong/Inflect-Nano-v2-ONNX``.
	repoId: str
	description: str


#: Voices that can be downloaded from the add-on settings panel. The
#: bundled Inflect Micro v2 is deliberately not listed here: it ships
#: inside the add-on package and is always available.
VOICE_CATALOG: tuple[VoiceSpec, ...] = (
	VoiceSpec(
		id="inflect-nano-v2",
		displayName="Inflect Nano v2",
		language="en",
		repoId="owensong/Inflect-Nano-v2-ONNX",
		description="A smaller, faster voice of the same Inflect TTS family (English, 24 kHz).",
	),
)

_availabilityCache: dict[str, tuple[float, bool]] = {}


def catalogVoices() -> tuple[VoiceSpec, ...]:
	"""All voices that can be downloaded from the settings panel."""
	return VOICE_CATALOG


def catalogVoice(voiceId: str) -> VoiceSpec | None:
	"""The catalog entry for ``voiceId``, or ``None`` if not downloadable."""
	for spec in VOICE_CATALOG:
		if spec.id == voiceId:
			return spec
	return None


def voiceDisplayName(voiceId: str) -> str:
	"""A human-readable name for a voice id (bundled or downloadable)."""
	if voiceId == BUNDLED_VOICE_ID:
		return "Inflect Micro v2"
	spec = catalogVoice(voiceId)
	if spec is not None:
		return spec.displayName
	return voiceId


def isInstalled(voiceId: str) -> bool:
	"""Whether a downloadable voice is fully present in the user folder."""
	return isComplete(userModelDir(voiceId))


def _resolveUrl(repoId: str, rel: str) -> str:
	return f"https://huggingface.co/{repoId}/resolve/main/{rel}"


def checkAvailable(voiceId: str, timeout: float = _REQUEST_TIMEOUT) -> bool:
	"""Whether the voice's model repository is reachable (cached briefly)."""
	spec = catalogVoice(voiceId)
	if spec is None:
		return False
	now = time.monotonic()
	cached = _availabilityCache.get(voiceId)
	if cached is not None and now - cached[0] < AVAILABILITY_CACHE_SECONDS:
		return cached[1]
	available = False
	try:
		request = urllib.request.Request(
			f"https://huggingface.co/api/models/{spec.repoId}",
			headers={"User-Agent": _USER_AGENT},
		)
		with urllib.request.urlopen(request, timeout=timeout) as response:
			available = response.status == 200
	except (urllib.error.URLError, OSError):
		# HTTPError is a subclass of URLError, so both are covered.
		available = False
	_availabilityCache[voiceId] = (now, available)
	return available


#: Progress callback: ``(relativePath, bytesDone, bytesTotal)``.
ProgressCallback = Callable[[str, int, int], None]


def downloadVoice(voiceId: str, progress: ProgressCallback | None = None) -> Path:
	"""Download and install a voice into the per-user models folder.

	Files are downloaded into a sibling temporary directory first, so a
	failed or interrupted download never leaves a half-installed voice
	behind. The set is verified against :data:`provision.MODEL_FILES` and
	only then moved into place.

	``progress``, if given, is called on the calling thread for every
	read chunk with ``(relativePath, bytesDone, bytesTotal)`` per file.
	"""
	spec = catalogVoice(voiceId)
	if spec is None:
		raise EngineError(f"Unknown voice: {voiceId}")
	target = userModelDir(voiceId)
	temp = target.with_name(target.name + ".tmp")
	if temp.exists():
		shutil.rmtree(temp, ignore_errors=True)
	temp.mkdir(parents=True)
	try:
		for rel in MODEL_FILES:
			dest = temp / rel
			dest.parent.mkdir(parents=True, exist_ok=True)
			request = urllib.request.Request(
				_resolveUrl(spec.repoId, rel),
				headers={"User-Agent": _USER_AGENT},
			)
			with (
				urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response,
				dest.open("wb") as out,
			):
				total = int(response.headers.get("Content-Length") or 0)
				done = 0
				while True:
					chunk = response.read(256 * 1024)
					if not chunk:
						break
					out.write(chunk)
					done += len(chunk)
					if progress is not None:
						progress(rel, done, total)
		if not isComplete(temp):
			raise EngineError(f"The download of '{voiceId}' is incomplete and was discarded.")
		if target.exists():
			shutil.rmtree(target, ignore_errors=True)
		temp.rename(target)
	except Exception:
		shutil.rmtree(temp, ignore_errors=True)
		raise
	return target


def removeVoice(voiceId: str) -> None:
	"""Delete a downloaded voice from the per-user models folder."""
	target = userModelDir(voiceId)
	if target.exists():
		shutil.rmtree(target, ignore_errors=True)
