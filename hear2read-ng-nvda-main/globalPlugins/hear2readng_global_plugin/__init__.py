# coding: utf-8

# Copyright (c) 2023 Musharraf Omer
# This file is covered by the GNU General Public License.

# A part of the Hear2Read Indic Voices addon for NVDA
# Copyright (C) 2013-2024, Hear2Read Project Contributors
# See the file COPYING for more details.

# this is a slightly modified version of the corresponding file in the sonata
# project (https://github.com/mush42/sonata-nvda)

from threading import Thread

import api
import braille
import config
import controlTypes
import core
import globalCommands
import globalPluginHandler
import gui
import inputCore
import queueHandler

# from urllib import request
import scriptHandler
import speech
import textInfos
import treeInterceptorHandler
import ui
import wx
from gui.addonGui import promptUserForRestart
from gui.message import DisplayableError
from logHandler import log
from scriptHandler import script
from synthDriverHandler import findAndSetNextSynth, getSynth, synthChanged
from utils.security import objectBelowLockScreenAndWindowsIsLocked

from globalPlugins.hear2readng_global_plugin.english_settings import (
    EnglishSpeechSettingsDialog,
)
from globalPlugins.hear2readng_global_plugin.file_utils import ADDON_NAME
from globalPlugins.hear2readng_global_plugin.h2rutils import (
    ID_ShowStartupPopup,
    SCT_General,
    _h2r_config,
    fetch_server_voices,
    postUpdateCheck,
    showStartupInfoDialog,
)
from globalPlugins.hear2readng_global_plugin.voice_manager import (
    Hear2ReadNGVoiceManagerDialog,
)
from synthDrivers._H2R_NG_Speak import getCurrentVoice

# from .voice_manager import Hear2ReadNGVoiceManagerDialog
SCRCAT_TEXTREVIEW = _("Text review")

curr_synth_name = ""
showNewUserMessage = True

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self, *args, **kwargs):
        global curr_synth_name, showNewUserMessage
        super().__init__(*args, **kwargs)
        self.__voice_manager_shown = False
        try:
            showNewUserMessage = _h2r_config[SCT_General][ID_ShowStartupPopup]
        except KeyError as e:
            log.error("H2R Config not loaded, this should not happen")
            confspec = {
                "engSynth": "string(default='oneCore')",
                "engVoice": "string(default='')",
                "engVariant": "string(default='')",
                "engRate": "integer(default=50)",
                "engPitch": "integer(default=50)",
                "engVolume": "integer(default=100)",
                "engInflection": "integer(default=80)",
                "showStartupMsg": "boolean(default=True)"
            }
            config.conf.spec["hear2read"] = confspec
            config.conf.save()
            showNewUserMessage = True

        curr_synth_name = getSynth().name
        self._voice_checker = lambda: wx.CallLater(2000, 
                                                    self._startup)
        core.postNvdaStartup.register(self._voice_checker)

        self.itemHandle = gui.mainFrame.sysTrayIcon.menu.Insert(
            4,
            wx.ID_ANY,
            # Translators: label of a menu item
            _("Hear2Read Voice Manager..."),
            # Translators: Hear2Read Indic's voice manager menu item help
            _("Open the voice manager to download Hear2Read Indic voices"),
        )
        gui.mainFrame.sysTrayIcon.menu.Bind(wx.EVT_MENU, self.on_manager, 
                                            self.itemHandle)
        
        self.eng_settings_active = False
        self.eng_settings_id = wx.Window.NewControlId()

        if ADDON_NAME in curr_synth_name:
            self.make_eng_settings_menu()
            self.eng_settings_active = True
        
        synthChanged.register(self.on_synth_changed)

    def on_synth_changed(self, synth):
        global curr_synth_name
        curr_synth_name = synth.name
        if ADDON_NAME in curr_synth_name:
            self.make_eng_settings_menu()
            self.eng_settings_active = True
            self._perform_voice_check()
            
        elif self.eng_settings_active:
            gui.mainFrame.sysTrayIcon.menu.Remove(self.eng_settings_id)
            self.eng_settings_active = False

    def make_eng_settings_menu(self):
        self.itemHandle = gui.mainFrame.sysTrayIcon.menu.Insert(
                5,
                self.eng_settings_id,
                # Translators: label of a menu item
                _("Hear2Read English Voice Settings..."),
                # Translators: Hear2Read Indic's voice manager menu item help
                _("Open the settings for the English voice used in Hear2Read"),
            )
        gui.mainFrame.sysTrayIcon.menu.Bind(wx.EVT_MENU,
                                lambda e : gui.mainFrame.popupSettingsDialog(
                                    EnglishSpeechSettingsDialog), 
                                self.itemHandle)

    def on_manager(self, event=None):
        manager_dialog = Hear2ReadNGVoiceManagerDialog()
        try:
            gui.runScriptModalDialog(manager_dialog, callback=self.on_manager_close)
            self.__voice_manager_shown = True
        except Exception as e:
            log.error(f"Failed to open Manager: {e}")

    def on_manager_close(self, res):
        if not any(Hear2ReadNGVoiceManagerDialog.get_installed_voices()):
            self.on_no_voices(getSynth().name)
        if(res == wx.ID_SETUP):
            promptUserForRestart()

    def on_no_voices(self, curr_synth_name):
        
        if ADDON_NAME in curr_synth_name:    
            msg_res = gui.messageBox(
                # Translators: message telling the user that no voice is installed
                _(
                    "No Indic Hear2Read voice was found.\n"
                    "Please download a voice from the manager to continue using Hear2Read Synthesizer.\n"
                    "Do you want to open the voice manager now?"
                ),
                # Translators: title of a message telling the user that no Hear2Read Indic voice was found
                _("Hear2Read Indic Voices"),
                wx.YES_NO | wx.ICON_WARNING,)
            
            if msg_res == wx.YES:
                wx.CallAfter(self.on_manager)
            else:
                noVoiceDisplayError = DisplayableError(
                    titleMessage="Shutting Hear2Read Down", 
                    displayMessage="No Hear2Read voices found. \n"
                    "Please install voices to use Hear2Read. \n"
                    "Voices can be installed from the Hear2Read voice manager in the NVDA menu")
                noVoiceDisplayError.displayError(gui.mainFrame)
                queueHandler.queueFunction(queueHandler.eventQueue, findAndSetNextSynth, curr_synth_name)
        else:
            if wx.YES == gui.messageBox(
                # Translators: message telling the user that no voice is installed
                _(
                    "No Indic Hear2Read voice was found.\n"
                    "Please select a Hear2Read voice to download from the manager.\n"
                    "Do you want to open the voice manager now?"
                ),
                # Translators: title of a message telling the user that no Hear2Read Indic voice was found
                _("Hear2Read Indic Voices"),
                wx.YES_NO | wx.ICON_WARNING,):
                wx.CallAfter(self.on_manager)

    def on_voice_update(self, lang):

        if self.__voice_manager_shown:# or gui.isModalMessageBoxActive():
            return
        
        if wx.YES == gui.messageBox(
                # Translators: message telling the user that no voice is installed
                _(
                    f"Update found for installed {lang} Hear2Read voice.\n"
                    "Updated voice is available in the Hear2Read voice manager.\n"
                    "Do you want to open the voice manager now?"
                ),
                # Translators: title of a message telling the user that no Hear2Read Indic voice was found
                _("Hear2Read Voice Update"),
                wx.YES_NO | wx.ICON_INFORMATION,):
            wx.CallAfter(self.on_manager)

    def _startup(self):

        if _h2r_config[SCT_General][ID_ShowStartupPopup]:
            # log.info("_start_checks: showNewUserMessage")
            wx.CallAfter(showStartupInfoDialog())
        
        self._perform_checks()

    def _on_startupinfo_closed(self, res):
        self._perform_checks()

    def _perform_checks(self):
        # log.info("_perform_checks")
        postUpdateCheck()
        self._perform_voice_check()
        
        if self.__voice_manager_shown:
            return
        
        self._perform_voice_update_check()

    # @gui.blockAction.when(gui.blockAction.Context.MODAL_DIALOG_OPEN)         
    def _perform_voice_check(self):
        if self.__voice_manager_shown:
            return

        if not any(Hear2ReadNGVoiceManagerDialog.get_installed_voices()):
            curr_synth_name = getSynth().name
            self.on_no_voices(curr_synth_name)

    
    def _perform_voice_update_check(self):
        """Populated the list of voices available on the server. Modifies the 
        class attribute server_voices, a list of Voice objects. The operation
        is done in a background thread while a BusyInfo is displayed.
        """
        if self.__voice_manager_shown:# or gui.isModalMessageBoxActive():
            return

        def fetch():
            """Main function to fetch the voice list from the server. Sets the
            server_error_event/network_error_event attribute in case of failure 
            """
            try:
                server_voices = fetch_server_voices()
                if server_voices:
                    installed_voices = Hear2ReadNGVoiceManagerDialog.get_installed_voices()
                    for iso, installed_voice in installed_voices.items():
                        server_voice = server_voices.get(iso, None)
                        if server_voice and server_voice.id != installed_voice.id:
                            log.info(f"checking update on {installed_voice.id}, found: {server_voice.id}")
                            self.on_voice_update(server_voice.display_name)
                            return
            except:
                log.debugWarning("Hear2ReadNG unable to check for voices online")

        # TODO: move to NVDA execandpump?
        Thread(target=fetch, daemon=True).start()

    def terminate(self):
        try:
            gui.mainFrame.sysTrayIcon.menu.DestroyItem(self.itemHandle)
        except:
            pass
        

    ############################################################################
    # Scripts to enable spelling clarification
    # These scripts override native NVDA gestures, and care has been taken that
    # the fuctionality isn't affected outside of the Hear2Read addon and TTS
    ############################################################################
    
    @script(
        # Translators: Input help mode message for report current character under review cursor command.
        description=_(
            "Reports the character of the current navigator object where the review cursor is situated. "
            "Pressing twice reports a description or example of that character. "
            "Pressing three times reports the numeric value of the character in decimal and hexadecimal",
        ),
        category=globalCommands.SCRCAT_TEXTREVIEW,
        gestures=("kb:numpad2", "kb(laptop):NVDA+."),
        speakOnDemand=True,
    )
    def script_h2r_review_currentCharacter(self, gesture: inputCore.InputGesture):
        
        if ADDON_NAME not in curr_synth_name:
            globalCommands.commands.script_review_currentCharacter(gesture)
            return

        info = api.getReviewPosition().copy()
        # This script is available on the lock screen via getSafeScripts, as such
        # ensure the review position does not contain secure information
        # before announcing this object
        if objectBelowLockScreenAndWindowsIsLocked(info.obj):
            ui.reviewMessage(gui.blockAction.Context.WINDOWS_LOCKED.translatedMessage)
            return

        info.expand(textInfos.UNIT_CHARACTER)
        scriptCount = scriptHandler.getLastScriptRepeatCount()
        # log.info(f"script_review_currentCharacter interrupt: {scriptCount}, info: {info.text}")
        
        if scriptCount == 1:
            try:
                lang = getCurrentVoice().split("-")[0]
                # Explicitly tether here
                braille.handler.handleReviewMove(shouldAutoTether=True)
                speech.speakSpelling(info.text, locale=lang, useCharacterDescriptions=True)
            except:
                globalCommands.commands.script_review_currentCharacter(gesture)
        else:
            globalCommands.commands.script_review_currentCharacter(gesture)

    
    @script(
        # Translators: Input help mode message for report current word under review cursor command.
        description=_(
            "Speaks the word of the current navigator object where the review cursor is situated. "
            "Pressing twice spells the word. "
            "Pressing three times spells the word using character descriptions",
        ),
        category=globalCommands.SCRCAT_TEXTREVIEW,
        gestures=("kb:numpad5", "kb(laptop):NVDA+control+.", "ts(text):hoverUp"),
        speakOnDemand=True,
    )
    def script_h2r_review_currentWord(self, gesture: inputCore.InputGesture):

        if ADDON_NAME not in curr_synth_name:
            globalCommands.commands.script_review_currentWord(gesture)
            return
        
        info = api.getReviewPosition().copy()
        # This script is available on the lock screen via getSafeScripts, as such
        # ensure the review position does not contain secure information
        # before announcing this object
        if objectBelowLockScreenAndWindowsIsLocked(info.obj):
            ui.reviewMessage(gui.blockAction.Context.WINDOWS_LOCKED.translatedMessage)
            return

        info.expand(textInfos.UNIT_WORD)
        # Explicitly tether here
        braille.handler.handleReviewMove(shouldAutoTether=True)
        scriptCount = scriptHandler.getLastScriptRepeatCount()
        if scriptCount == 0:
            speech.speakTextInfo(info, reason=controlTypes.OutputReason.CARET, unit=textInfos.UNIT_WORD)
        elif scriptCount == 1:
            speech.spellTextInfo(info, useCharacterDescriptions=False)
        else:
            try:
                lang = getCurrentVoice().split("-")[0]
                speech.speakSpelling(info.text, locale=lang, useCharacterDescriptions=True)
            except:
                speech.speakSpelling(info.text, useCharacterDescriptions=True)

    @script(
        # Translators: Input help mode message for read current line under review cursor command.
        description=_(
            "Reports the line of the current navigator object where the review cursor is situated. "
            "If this key is pressed twice, the current line will be spelled. "
            "Pressing three times will spell the line using character descriptions.",
        ),
        category=globalCommands.SCRCAT_TEXTREVIEW,
        gestures=("kb:numpad8", "kb(laptop):NVDA+shift+."),
        speakOnDemand=True,
    )
    def script_h2r_review_currentLine(self, gesture: inputCore.InputGesture):

        if ADDON_NAME not in curr_synth_name:
            globalCommands.commands.script_review_currentLine(gesture)
            return
        
        info = api.getReviewPosition().copy()
        # This script is available on the lock screen via getSafeScripts, as such
        # ensure the review position does not contain secure information
        # before announcing this object
        if objectBelowLockScreenAndWindowsIsLocked(info.obj):
            ui.reviewMessage(gui.blockAction.Context.WINDOWS_LOCKED.translatedMessage)
            return
        info.expand(textInfos.UNIT_LINE)
        # Explicitly tether here
        braille.handler.handleReviewMove(shouldAutoTether=True)
        scriptCount = scriptHandler.getLastScriptRepeatCount()
        if scriptCount == 0:
            speech.speakTextInfo(info, unit=textInfos.UNIT_LINE, reason=controlTypes.OutputReason.CARET)
        elif scriptCount == 1:
            speech.spellTextInfo(info, useCharacterDescriptions=False)
        else:
            try:
                lang = getCurrentVoice().split("-")[0]
                speech.speakSpelling(info.text, locale=lang, useCharacterDescriptions=True)
            except:
                speech.speakSpelling(info.text, useCharacterDescriptions=True)

    @script(
        # Translators: Input help mode message for report current line command.
        description=_(
            "Reports the current line under the application cursor. "
            "Pressing this key twice will spell the current line. "
            "Pressing three times will spell the line using character descriptions.",
        ),
        category=globalCommands.SCRCAT_SYSTEMCARET,
        gestures=("kb(desktop):NVDA+upArrow", "kb(laptop):NVDA+l"),
        speakOnDemand=True,
    )
    def script_h2r_reportCurrentLine(self, gesture):
        if ADDON_NAME not in curr_synth_name:
            globalCommands.commands.script_reportCurrentLine(gesture)
            return
        
        obj = api.getFocusObject()
        treeInterceptor = obj.treeInterceptor
        if (
            isinstance(treeInterceptor, treeInterceptorHandler.DocumentTreeInterceptor)
            and not treeInterceptor.passThrough
        ):
            obj = treeInterceptor
        try:
            info = obj.makeTextInfo(textInfos.POSITION_CARET)
        except (NotImplementedError, RuntimeError):
            info = obj.makeTextInfo(textInfos.POSITION_FIRST)
        info.expand(textInfos.UNIT_LINE)
        scriptCount = scriptHandler.getLastScriptRepeatCount()
        if scriptCount == 0:
            speech.speakTextInfo(info, unit=textInfos.UNIT_LINE, reason=controlTypes.OutputReason.CARET)
        elif scriptCount == 1:
            speech.spellTextInfo(info, useCharacterDescriptions=False)
        else:
            try:
                lang = getCurrentVoice().split("-")[0]
                speech.speakSpelling(info.text, locale=lang, useCharacterDescriptions=True)
            except:
                speech.speakSpelling(info.text, useCharacterDescriptions=True)
        
    @script(
        # Translators: Input help mode message for report current selection command.
        description=_(
            "Announces the current selection in edit controls and documents. "
            "Pressing twice spells this information. "
            "Pressing three times spells it using character descriptions. "
            "Pressing four times shows it in a browsable message. ",
        ),
        category=globalCommands.SCRCAT_SYSTEMCARET,
        gestures=("kb(desktop):NVDA+shift+upArrow", "kb(laptop):NVDA+shift+s"),
        speakOnDemand=True,
    )
    def script_h2r_reportCurrentSelection(self, gesture):
        if ADDON_NAME not in curr_synth_name:
            globalCommands.commands.script_reportCurrentSelection(gesture)
            return
        
        obj = api.getFocusObject()
        treeInterceptor = obj.treeInterceptor
        if (
            isinstance(treeInterceptor, treeInterceptorHandler.DocumentTreeInterceptor)
            and not treeInterceptor.passThrough
        ):
            obj = treeInterceptor
        try:
            info = obj.makeTextInfo(textInfos.POSITION_SELECTION)
        except (RuntimeError, NotImplementedError):
            info = None
        if not info or info.isCollapsed:
            # Translators: The message reported when there is no selection
            ui.message(_("No selection"))
        else:
            scriptCount = scriptHandler.getLastScriptRepeatCount()
            # Translators: The message reported after selected text
            selectMessage = speech.speech._getSelectionMessageSpeech(_("%s selected"), info.text)[0]
            if scriptCount == 0:
                speech.speakTextSelected(info.text)
                braille.handler.message(selectMessage)
            elif scriptCount == 3:
                ui.browseableMessage(info.text, copyButton=True, closeButton=True)
                return

            elif len(info.text) < speech.speech.MAX_LENGTH_FOR_SELECTION_REPORTING:
                if scriptCount == 1:
                    speech.speakSpelling(info.text, useCharacterDescriptions=False)
                else:
                    try:
                        lang = getCurrentVoice().split("-")[0]
                        speech.speakSpelling(info.text, locale=lang, useCharacterDescriptions=True)
                    except:
                        speech.speakSpelling(info.text, useCharacterDescriptions=True)
            else:
                speech.speakTextSelected(info.text)
                braille.handler.message(selectMessage)

    ############################################################################
    # end of scripts section
    ############################################################################