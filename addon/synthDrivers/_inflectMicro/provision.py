# addon/synthDrivers/_inflectMicro/provision.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Locate the Inflect TTS model and text frontend files.

The add-on is designed to work fully offline: one voice (Inflect Micro v2)
ships inside the add-on package under ``model/`` (populated at build time
by ``tools/fetch_model.py``). Additional voices of the same Inflect TTS
family can be downloaded from the add-on settings into a per-user folder;
each voice is a complete model directory.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Runtime artifacts that must all be present for a voice to work.
#: This is the minimal subset of the official
#: ``owensong/Inflect-*-v2-ONNX`` repositories needed for ONNX inference.
MODEL_FILES: tuple[str, ...] = (
	"onnx/duration.onnx",
	"onnx/decode.onnx",
	"config.json",
	"inflect_vits_frontend.py",
	"inflect_nano_v2_frontend.py",
	"runtime/text/__init__.py",
	"runtime/text/cleaners.py",
	"runtime/text/symbols.py",
)

#: Identifier of the voice bundled inside the add-on package.
BUNDLED_VOICE_ID = "inflect-micro-v2"


class EngineError(Exception):
	"""Raised when the bundled model or its dependencies cannot be used."""


def _packageDir() -> Path:
	return Path(__file__).resolve().parent


def bundledModelDir() -> Path:
	"""The model directory shipped inside the add-on package."""
	return _packageDir() / "model"


def userModelsRoot() -> Path:
	"""Root folder holding user-downloaded voices."""
	base = Path(os.environ.get("APPDATA") or Path.home())
	return base / "inflectMicroTTS" / "models"


def userModelDir(voiceId: str) -> Path:
	"""The per-user model directory for a downloaded voice."""
	return userModelsRoot() / voiceId


def isComplete(modelDir: Path) -> bool:
	return all((modelDir / rel).is_file() for rel in MODEL_FILES)


def bundledVoiceInstalled() -> bool:
	return isComplete(bundledModelDir())


def installedVoiceIds() -> list[str]:
	"""IDs of voices whose model files are present (bundled voice first)."""
	ids = [BUNDLED_VOICE_ID] if bundledVoiceInstalled() else []
	root = userModelsRoot()
	if root.is_dir():
		for child in sorted(root.iterdir()):
			if child.is_dir() and isComplete(child):
				ids.append(child.name)
	return ids


def findModelDir(voiceId: str | None = None) -> Path:
	"""Return the model directory for ``voiceId`` (default: bundled voice)."""
	if voiceId is None or voiceId == BUNDLED_VOICE_ID:
		return bundledModelDir()
	user = userModelDir(voiceId)
	if isComplete(user):
		return user
	raise EngineError(
		f"The model files for voice '{voiceId}' were not found. "
		f"Expected them in {user}."
	)
