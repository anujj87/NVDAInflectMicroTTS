# addon/globalPlugins/inflectMicroTTS/__init__.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Global plugin that registers the Inflect Micro TTS settings panel.

The panel (see :mod:`.settingsPanel`) lets the user download additional
voices of the Inflect TTS family, remove them again and apply a voice to
the current synthesizer -- all from NVDA Settings.
"""

from __future__ import annotations

import addonHandler
import globalPluginHandler
from gui.settingsDialogs import NVDASettingsDialog
from synthDrivers._inflectMicro import providers

from .settingsPanel import InflectMicroTTSSettingsPanel

addonHandler.initTranslation()

# Register the add-on's configuration section (the compute device
# setting) with NVDA so it is validated and saved per configuration
# profile.
providers.ensureSpec()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	"""Registers the voice management panel in NVDA Settings."""

	def __init__(self) -> None:
		super().__init__()
		NVDASettingsDialog.categoryClasses.append(InflectMicroTTSSettingsPanel)

	def terminate(self) -> None:
		try:
			NVDASettingsDialog.categoryClasses.remove(InflectMicroTTSSettingsPanel)
		except ValueError:
			pass
		super().terminate()
