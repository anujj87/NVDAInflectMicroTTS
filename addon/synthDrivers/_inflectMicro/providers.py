# addon/synthDrivers/_inflectMicro/providers.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Execution-provider (CPU/GPU) detection and configuration.

The add-on bundles onnxruntime-directml, a single ONNX Runtime build that
ships both the CPU and the DirectML (GPU) execution providers, so ``import
onnxruntime`` exposes both. DirectML runs on any Direct3D 12 capable GPU
(NVIDIA, AMD, Intel) without a CUDA toolkit. Because DirectML advertises
itself even on machines without a usable DX12 device, each non-CPU
provider is honestly probed here by creating a real InferenceSession for a
tiny embedded model (:mod:`._probe_model`) before it is offered in the
settings panel; the probe result is cached for the NVDA session.

The user's choice lives in NVDA's configuration under the
``inflectMicroTTS`` section:

* ``"auto"`` (default) - the best usable provider (GPU when available,
  else CPU);
* ``"cpu"`` - always available;
* ``"gpu"`` - DirectML, only listed in the settings panel when the probe
  succeeds.

This module only uses the standard library at import time (numpy and
onnxruntime are pulled in lazily), so it stays importable in the standalone
test harnesses that run outside NVDA.
"""

from __future__ import annotations

import base64
import logging
import threading
import warnings

from . import _probe_model

_logger = logging.getLogger("inflectMicroTTS")

#: Canonical provider ids used in the add-on's configuration.
CPU = "cpu"
GPU = "gpu"

#: Provider id -> ONNX Runtime provider name(s), in priority order.
_PROVIDER_ORT_NAMES: dict[str, tuple[str, ...]] = {
	CPU: ("CPUExecutionProvider",),
	GPU: ("DmlExecutionProvider",),
}

#: Display order in the settings panel (CPU is always first).
ORDER: tuple[str, ...] = (CPU, GPU)

#: The add-on's section in NVDA's configuration. Registered with NVDA's
#: config system by :func:`ensureSpec` so the setting participates in
#: configuration profiles and is validated on load.
CONFIG_SECTION = "inflectMicroTTS"

SPEC: dict[str, str] = {
	# Which execution provider renders speech: "auto", "cpu" or "gpu".
	"provider": "string(default='auto')",
}

_cache: tuple[str, ...] | None = None
_cacheLock = threading.Lock()


def _probeProvider(ortName: str) -> bool:
	"""True when ``ortName`` can actually run a real ONNX session.

	Creates an InferenceSession for the tiny embedded probe model with
	the provider first and CPU as fallback, then runs it once. DirectML
	advertises itself even on machines without a usable DX12 device and
	only fails at session creation, so this is the honest check. Takes
	tens of milliseconds; the result is cached by :func:`detectProviders`.
	"""
	try:
		import numpy as np  # pyright: ignore[reportMissingImports]  # vendored in lib/
		import onnxruntime as ort  # pyright: ignore[reportMissingImports]  # vendored in lib/

		modelBytes = base64.b64decode(_probe_model.PROBE_ONNX_B64)
		with warnings.catch_warnings():
			# On a CPU-only onnxruntime build ORT prints a UserWarning
			# ("Specified provider ... not in available provider names")
			# before raising; that is the expected outcome here, so
			# keep the log clean.
			warnings.simplefilter("ignore")
			sess = ort.InferenceSession(
				modelBytes, providers=[ortName, "CPUExecutionProvider"]
			)
		# ORT silently falls back to CPU when the requested provider is
		# not available at all (e.g. a CPU-only onnxruntime build); only
		# a session that actually adopted the provider counts as usable.
		if ortName not in sess.get_providers():
			_logger.debug("Inflect Micro TTS: provider %s not adopted; skipping", ortName)
			return False
		sess.run(None, {"x": np.ones((1, 4), dtype=np.float32)})
		return True
	except Exception:
		_logger.debug("Inflect Micro TTS: provider probe failed for %s", ortName, exc_info=True)
		return False


def detectProviders(force: bool = False) -> tuple[str, ...]:
	"""The usable provider ids, most preferred first.

	The result is cached for the NVDA session; pass ``force=True`` to
	re-probe (e.g. after the bundled runtime changes). Never raises - on
	total failure only CPU is returned.
	"""
	global _cache
	with _cacheLock:
		if _cache is not None and not force:
			return _cache
		usable = [CPU]
		for pid in ORDER:
			if pid == CPU:
				continue
			if any(_probeProvider(name) for name in _PROVIDER_ORT_NAMES[pid]):
				usable.append(pid)
		_cache = tuple(usable)
		_logger.debug("Inflect Micro TTS: usable providers: %s", _cache)
		return _cache


def isAvailable(provider: str) -> bool:
	"""Whether ``provider`` is usable on this machine."""
	return provider in detectProviders()


def defaultProvider() -> str:
	"""The best usable provider: GPU when available, else CPU."""
	if GPU in detectProviders():
		return GPU
	return CPU


def ortProvidersFor(provider: str) -> list[str]:
	"""The ONNX Runtime provider list for a provider id.

	GPU gets ``["DmlExecutionProvider", "CPUExecutionProvider"]`` so the
	nodes DirectML cannot run fall back to CPU; CPU gets the CPU provider
	alone. The list is returned as plain names, which is what
	``InferenceSession(providers=...)`` accepts.
	"""
	if provider == GPU:
		return ["DmlExecutionProvider", "CPUExecutionProvider"]
	return ["CPUExecutionProvider"]


def getProvider() -> str:
	"""Resolve the configured provider to a concrete provider id.

	``"auto"`` maps to the best usable provider (GPU when available,
	else CPU). A configured value that is not usable on this machine also
	resolves through ``"auto"`` rather than silently breaking speech.
	"""
	value = getSetting("provider", "auto")
	if value in (None, ""):
		value = "auto"
	if value != "auto":
		if isAvailable(value):
			return value
		_logger.debug("Inflect Micro TTS: provider %s is not usable; using the best available", value)
	return defaultProvider()


# ---------------- NVDA configuration ----------------

def ensureSpec() -> None:
	"""Register the add-on's configuration section with NVDA.

	Safe to call repeatedly; does nothing when NVDA's config is not
	available (e.g. in standalone test harnesses).
	"""
	try:
		import config  # pyright: ignore[reportMissingImports]  # NVDA's config module

		if CONFIG_SECTION not in config.conf.spec:
			config.conf.spec[CONFIG_SECTION] = SPEC
	except Exception:
		pass


def getSetting(key: str, default=None):
	"""Read a value from the add-on's NVDA config section."""
	ensureSpec()
	try:
		import config  # pyright: ignore[reportMissingImports]  # NVDA's config module

		return config.conf[CONFIG_SECTION].get(key, default)
	except Exception:
		return default


def setSetting(key: str, value) -> None:
	"""Write a value into the add-on's NVDA config section."""
	ensureSpec()
	try:
		import config  # pyright: ignore[reportMissingImports]  # NVDA's config module

		config.conf[CONFIG_SECTION][key] = value
	except Exception:
		_logger.debug("Inflect Micro TTS: failed to save config %s=%r", key, value, exc_info=True)
