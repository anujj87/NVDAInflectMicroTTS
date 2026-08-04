# addon/synthDrivers/_inflectMicro/__init__.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Bundled engine for the Inflect Micro TTS add-on.

This package name is prefixed with an underscore so that NVDA never tries
to load it as a separate speech synthesizer driver.

Third-party Python packages (onnxruntime, numpy, phonemizer, num2words,
Unidecode, espeakng-loader, ...) are vendored in the ``lib`` sub-folder by
``tools/vendor_deps.py`` and made importable here, before any of the engine
modules import them.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "lib"
if str(_LIB_DIR) not in sys.path:
	# Insert at the front so that the bundled versions win over any
	# system-wide packages NVDA may already expose.
	sys.path.insert(0, str(_LIB_DIR))


def _ensureConcurrentFuturesProcessCompat() -> None:
	"""Allow joblib/loky to import under NVDA's trimmed standard library.

	NVDA's bundled Python omits ``concurrent.futures.process``, which the
	vendored joblib (imported by phonemizer at synthesis time) needs at
	import time. The engine never spawns a process pool -- phonemization
	is always single-job -- so a minimal shim providing the two names
	joblib/loky import is enough.
	"""
	if "concurrent.futures.process" in sys.modules:
		return
	try:
		importlib.import_module("concurrent.futures.process")
		return
	except ModuleNotFoundError:
		pass  # NVDA's trimmed stdlib; install the shim below
	stub = types.ModuleType("concurrent.futures.process")
	# Mirrors CPython's concurrent/futures/process.py: BrokenProcessPool
	# derives from RuntimeError and Windows process pools are capped at 61
	# workers. Neither is exercised here (no process pools are ever used),
	# so these are simple stand-ins.
	stub.BrokenProcessPool = type("BrokenProcessPool", (RuntimeError,), {})
	stub._MAX_WINDOWS_WORKERS = 61
	sys.modules["concurrent.futures.process"] = stub


_ensureConcurrentFuturesProcessCompat()
