# addon/globalPlugins/inflectMicroTTS/settingsPanel.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Settings panel for managing Inflect TTS voices.

Shows the bundled voice plus every voice offered by the download catalog
(:mod:`synthDrivers._inflectMicro.voices`) with its current status, lets
the user download a new voice (with progress feedback), remove a
downloaded one again, and apply a voice to the active synthesizer.
"""

from __future__ import annotations

import threading

import addonHandler
import config
import gui
import synthDriverHandler
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from synthDrivers._inflectMicro import provision, voices

addonHandler.initTranslation()


class InflectMicroTTSSettingsPanel(SettingsPanel):
	# Translators: Title of this category in NVDA's Settings dialog.
	title = _("Inflect Micro TTS")

	def __init__(self, parent: wx.Window) -> None:
		self._voiceIds: list[str] = []
		#: voiceId -> availability result (True/False/None = not checked).
		self._availability: dict[str, bool | None] = {}
		self._busy = False
		super().__init__(parent)
		self._refreshVoices()

	def makeSettings(self, settingsSizer: wx.BoxSizer) -> None:
		sHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		# Translators: Label for the list of voices on this settings panel.
		self.voicesList = sHelper.addLabeledControl(
			_("&Voices:"),
			wx.ListCtrl,
			style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
			size=self.scaleSize((420, 200)),
		)
		# Translators: Column header of the voice list.
		self.voicesList.InsertColumn(0, _("Voice"))
		# Translators: Column header of the voice list.
		self.voicesList.InsertColumn(1, _("Status"))
		self.voicesList.SetColumnWidth(0, 250)
		self.voicesList.SetColumnWidth(1, 130)
		self.voicesList.Bind(wx.EVT_LIST_ITEM_SELECTED, self._onSelectionChanged)

		bHelper = guiHelper.ButtonHelper(wx.HORIZONTAL)
		# Translators: Button to download the selected voice.
		self.downloadButton = bHelper.addButton(self, label=_("&Download"))
		self.downloadButton.Bind(wx.EVT_BUTTON, self._onDownload)
		# Translators: Button to remove the selected downloaded voice.
		self.removeButton = bHelper.addButton(self, label=_("&Remove"))
		self.removeButton.Bind(wx.EVT_BUTTON, self._onRemove)
		# Translators: Button to apply the selected voice to the synthesizer.
		self.applyButton = bHelper.addButton(self, label=_("&Apply"))
		self.applyButton.Bind(wx.EVT_BUTTON, self._onApply)
		# Translators: Button to re-check which voices are available online.
		self.refreshButton = bHelper.addButton(self, label=_("&Refresh availability"))
		self.refreshButton.Bind(wx.EVT_BUTTON, self._onRefreshAvailability)
		sHelper.addItem(bHelper)

		self.progressGauge = sHelper.addItem(wx.Gauge(self, range=100))
		self.progressGauge.Hide()
		# Translators: Status text shown below the voice list.
		self.statusLabel = sHelper.addItem(wx.StaticText(self, label=_("Ready.")))

	def onPanelActivated(self) -> None:
		super().onPanelActivated()
		self._refreshVoices()
		self._checkAvailability()

	def onSave(self) -> None:
		# All actions (download/remove/apply) take effect immediately on
		# their buttons; there is nothing to defer to the dialog's OK.
		pass

	# ---------------- voice list ----------------

	def _isInstalled(self, voiceId: str) -> bool:
		if voiceId == provision.BUNDLED_VOICE_ID:
			return provision.bundledVoiceInstalled()
		return voices.isInstalled(voiceId)

	def _refreshVoices(self) -> None:
		installed = set(provision.installedVoiceIds())
		self._voiceIds = [provision.BUNDLED_VOICE_ID]
		self._voiceIds.extend(spec.id for spec in voices.catalogVoices())
		self.voicesList.DeleteAllItems()
		for index, voiceId in enumerate(self._voiceIds):
			name = voices.voiceDisplayName(voiceId)
			if voiceId == provision.BUNDLED_VOICE_ID:
				# Translators: Status of the voice shipped with the add-on.
				status = _("Installed (bundled)") if voiceId in installed else _("Missing")
			elif voiceId in installed:
				# Translators: Status of an installed voice.
				status = _("Installed")
			elif self._availability.get(voiceId) is True:
				# Translators: Status of a voice that can be downloaded.
				status = _("Available")
			elif self._availability.get(voiceId) is False:
				# Translators: Status of a voice that is not reachable.
				status = _("Unavailable")
			else:
				# Translators: Status of a voice that has not been checked yet.
				status = _("Not checked")
			self.voicesList.InsertItem(index, name)
			self.voicesList.SetItem(index, 1, status)
		self._updateButtons()

	def _selectedVoiceId(self) -> str | None:
		sel = self.voicesList.GetFirstSelected()
		if sel < 0 or sel >= len(self._voiceIds):
			return None
		return self._voiceIds[sel]

	def _onSelectionChanged(self, evt: wx.ListEvent) -> None:
		self._updateButtons()

	def _updateButtons(self) -> None:
		voiceId = self._selectedVoiceId()
		self.downloadButton.Enable(
			voiceId is not None and voiceId != provision.BUNDLED_VOICE_ID and not self._busy
		)
		self.removeButton.Enable(
			voiceId is not None and voiceId != provision.BUNDLED_VOICE_ID and not self._busy
		)
		self.applyButton.Enable(voiceId is not None and not self._busy)
		self.refreshButton.Enable(not self._busy)

	def _setBusy(self, busy: bool) -> None:
		self._busy = busy
		self._updateButtons()

	# ---------------- availability ----------------

	def _checkAvailability(self) -> None:
		self._availability = {spec.id: None for spec in voices.catalogVoices()}
		# Translators: Status text while checking the download server.
		self.statusLabel.SetLabel(_("Checking availability..."))

		def worker() -> None:
			result: dict[str, bool] = {}
			for spec in voices.catalogVoices():
				result[spec.id] = voices.checkAvailable(spec.id, timeout=15)
			wx.CallAfter(self._onAvailabilityChecked, result)

		threading.Thread(target=worker, daemon=True, name="inflectMicroTTS-availability").start()

	def _onAvailabilityChecked(self, result: dict[str, bool]) -> None:
		# The panel may have been destroyed while the check thread was
		# running (e.g. the dialog was closed); guard against touching
		# dead wx controls.
		try:
			self._availability.update(result)
			self._refreshVoices()
			# Translators: Status text after checking the download server.
			self.statusLabel.SetLabel(_("Ready."))
		except Exception:
			log.debugWarning("Inflect Micro TTS: panel closed during availability check")

	def _onRefreshAvailability(self, evt: wx.CommandEvent) -> None:
		self._checkAvailability()

	# ---------------- download ----------------

	def _onDownload(self, evt: wx.CommandEvent) -> None:
		voiceId = self._selectedVoiceId()
		if voiceId is None:
			# Translators: Message when no voice is selected for download.
			gui.messageBox(
				_("Select a voice from the list first."),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		if self._isInstalled(voiceId):
			# Translators: Message when the selected voice is already installed.
			gui.messageBox(
				_("This voice is already installed."),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		self._setBusy(True)
		self.progressGauge.SetValue(0)
		self.progressGauge.Show()
		# Translators: Status text while a voice is downloading.
		self.statusLabel.SetLabel(_("Downloading..."))

		def worker() -> None:
			try:
				voices.downloadVoice(voiceId, progress=self._onDownloadProgress)
			except Exception as exc:
				log.debugWarning("Inflect Micro TTS: voice download failed", exc_info=exc)
				wx.CallAfter(self._onDownloadFailed, voiceId, exc)
			else:
				wx.CallAfter(self._onDownloadFinished, voiceId)

		threading.Thread(target=worker, daemon=True, name="inflectMicroTTS-download").start()

	def _onDownloadProgress(self, rel: str, done: int, total: int) -> None:
		# Called from the download thread; marshal to the GUI thread.
		def update() -> None:
			try:
				if total > 0:
					self.progressGauge.SetValue(int(done * 100 / total))
				# Translators: Status text while a voice is downloading.
				self.statusLabel.SetLabel(_("Downloading {}...").format(rel))
			except Exception:
				log.debugWarning("Inflect Micro TTS: panel closed during download")

		wx.CallAfter(update)

	def _onDownloadFinished(self, voiceId: str) -> None:
		try:
			self._setBusy(False)
			self.progressGauge.Hide()
			self._availability[voiceId] = True
			self._refreshVoices()
			# Translators: Status text after a successful download.
			self.statusLabel.SetLabel(_("Ready."))
			# Translators: Message after a voice has been downloaded.
			gui.messageBox(
				_(
					"The voice was downloaded successfully. "
					"Press Apply to use it now, or choose it in the Voice setting."
				),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
		except Exception:
			log.debugWarning("Inflect Micro TTS: panel closed after download completed")

	def _onDownloadFailed(self, voiceId: str, exc: Exception) -> None:
		try:
			self._setBusy(False)
			self.progressGauge.Hide()
			self._refreshVoices()
			# Translators: Status text after a failed download.
			self.statusLabel.SetLabel(_("Ready."))
			# Translators: Message when a voice download fails.
			gui.messageBox(
				_("The download failed: {error}").format(error=exc),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
		except Exception:
			log.debugWarning("Inflect Micro TTS: panel closed during failed download")

	# ---------------- remove ----------------

	def _onRemove(self, evt: wx.CommandEvent) -> None:
		voiceId = self._selectedVoiceId()
		if voiceId is None or voiceId == provision.BUNDLED_VOICE_ID:
			# Translators: Message when the user tries to remove the bundled voice.
			gui.messageBox(
				_("The voice bundled with the add-on cannot be removed."),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		if not self._isInstalled(voiceId):
			return
		# Translators: Confirmation prompt before removing a downloaded voice.
		if (
			gui.messageBox(
				_("Remove the {name} voice? Its model files will be deleted.").format(
					name=voices.voiceDisplayName(voiceId)
				),
				_("Inflect Micro TTS"),
				wx.YES_NO | wx.ICON_QUESTION,
				self,
			)
			!= wx.YES
		):
			return
		# Drop the cached ONNX sessions for this voice first: they may keep
		# the model files open/mapped on Windows, which would make the
		# directory removal silently fail. Imported lazily so the panel
		# never pulls in numpy/onnxruntime just to open.
		try:
			from synthDrivers._inflectMicro.engine import releaseEngine

			releaseEngine(voiceId)
		except Exception:
			log.debugWarning(
				"Inflect Micro TTS: failed to release the cached engine before removing a voice",
				exc_info=True,
			)
		voices.removeVoice(voiceId)
		self._availability.pop(voiceId, None)
		# If the removed voice was the active one, fall back to the bundled
		# voice so the synthesizer never points at a deleted model.
		synth = synthDriverHandler.getSynth()
		if synth is not None and synth.name == "inflectMicroTTS" and synth.voice == voiceId:
			try:
				synthDriverHandler.changeVoice(synth, provision.BUNDLED_VOICE_ID)
				synth.saveSettings()
				config.conf.save()
				# Translators: Status text after removing the active voice.
				self.statusLabel.SetLabel(_("Voice removed. Switched back to the bundled voice."))
			except Exception:
				log.exception("Inflect Micro TTS: failed to fall back after voice removal")
				# Translators: Status text when the fallback after removal failed.
				self.statusLabel.SetLabel(_("Voice removed, but the fallback failed."))
		else:
			# Translators: Status text after removing a voice.
			self.statusLabel.SetLabel(_("Voice removed."))
		self._refreshVoices()

	# ---------------- apply ----------------

	def _onApply(self, evt: wx.CommandEvent) -> None:
		voiceId = self._selectedVoiceId()
		if voiceId is None:
			# Translators: Message when no voice is selected to apply.
			gui.messageBox(
				_("Select a voice from the list first."),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		if not self._isInstalled(voiceId):
			# Translators: Message when the selected voice is not installed yet.
			gui.messageBox(
				_("This voice is not installed yet. Download it first."),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		synth = synthDriverHandler.getSynth()
		if synth is None or synth.name != "inflectMicroTTS":
			# Translators: Message when Inflect Micro TTS is not the active synthesizer.
			gui.messageBox(
				_(
					"Inflect Micro TTS is not the active synthesizer. "
					"Select it in NVDA Settings > Speech first."
				),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_INFORMATION,
				self,
			)
			return
		try:
			synthDriverHandler.changeVoice(synth, voiceId)
			synth.saveSettings()
			config.conf.save()
		except Exception:
			log.exception("Inflect Micro TTS: failed to apply voice")
			# Translators: Message when applying a voice fails.
			gui.messageBox(
				_("Failed to apply the voice."),
				_("Inflect Micro TTS"),
				wx.OK | wx.ICON_ERROR,
				self,
			)
			return
		# Translators: Status text after applying a voice.
		self.statusLabel.SetLabel(_("Voice applied."))
