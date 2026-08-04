import weakref
from locale import strxfrm
from typing import Any

import config
import gui
import wx
from autoSettingsUtils.driverSetting import DriverSetting, NumericDriverSetting
from gui import SettingsDialog, guiHelper, nvdaControls
from gui.settingsDialogs import (
    DriverSettingChanger,
    SettingsPanel,
    StringDriverSettingChanger,
)
from logHandler import log
from synthDriverHandler import getSynthInstance, getSynthList
from wx.lib.expando import ExpandoTextCtrl

from globalPlugins.hear2readng_global_plugin.h2rutils import (
    ID_EnglishSynthInflection,
    ID_EnglishSynthName,
    ID_EnglishSynthPitch,
    ID_EnglishSynthRate,
    ID_EnglishSynthVariant,
    ID_EnglishSynthVoice,
    ID_EnglishSynthVolume,
    SCT_EngSynth,
)
from synthDrivers._H2R_NG_Speak import (
    _h2r_config,
    get_eng_synth,
    get_eng_synth_desc,
    get_eng_synth_inflection,
    get_eng_synth_name,
    get_eng_synth_pitch,
    get_eng_synth_rate,
    get_eng_synth_variant,
    get_eng_synth_voice,
    get_eng_synth_voicelist,
    get_eng_synth_volume,
    set_eng_synth,
    set_eng_synth_inflection,
    set_eng_synth_pitch,
    set_eng_synth_rate,
    set_eng_synth_variant,
    set_eng_synth_voice,
    set_eng_synth_volume,
)

# TODO add loading for synth change
# TODO? add restart NVDA dialog

Eng_Synth = None

class EnglishSpeechSettingsDialog(SettingsDialog):
    """Setting dialog for English speech settings. This is an adaptation of the 
    code in gui.settingsDialogs.SpeechSettingsPanel, rewritten as a dialog.
    """
    # Translators: This is the label for the speech dialog
    title = _("English Speech")
    # helpId = "SpeechSettings"

    def __init__(self, parent):        
        super(EnglishSpeechSettingsDialog, self).__init__(
            parent,
            resizeable=True,
        )
        
        # setting the size must be done after the parent is constructed.
        self.SetMinSize(self.scaleSize(self.MIN_SIZE))
        self.SetSize(self.scaleSize(self.INITIAL_SIZE))
        # the size has changed, so recenter on the screen
        self.CentreOnScreen()
        
    # Initial / min size for the dialog. This size was chosen as a medium fit, so the
    # smaller settings panels are not surrounded by too much space but most of
    # the panels fit. Vertical scrolling is acceptable. Horizontal scrolling less
    # so, the width was chosen to eliminate horizontal scroll bars. If a panel
    # exceeds the the initial width a debugWarning will be added to the log.
    INITIAL_SIZE = (350, 370)
    MIN_SIZE = (350, 240) # Min height required to show the OK, Cancel, Apply buttons

    def makeSettings(self, settingsSizer):
        settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        # Translators: A label for the synthesizer on the speech panel.
        synthLabel = _("&Synthesizer")
        synthBoxSizer = wx.StaticBoxSizer(wx.HORIZONTAL, self, label=synthLabel)
        synthBox = synthBoxSizer.GetStaticBox()
        synthGroup = guiHelper.BoxSizerHelper(self, sizer=synthBoxSizer)
        settingsSizerHelper.addItem(synthGroup)

        # Use a ExpandoTextCtrl because even when readonly it accepts focus from keyboard, which
        # standard readonly TextCtrl does not. ExpandoTextCtrl is a TE_MULTILINE control, however
        # by default it renders as a single line. Standard TextCtrl with TE_MULTILINE has two lines,
        # and a vertical scroll bar. This is not neccessary for the single line of text we wish to
        # display here.
        synthDesc = get_eng_synth_desc()
        self.synthNameCtrl = ExpandoTextCtrl(
            synthBox,
            size=(self.scaleSize(250), -1),
            value=synthDesc,
            style=wx.TE_READONLY,
        )
        self.synthNameCtrl.Bind(wx.EVT_CHAR_HOOK, self._enterTriggersOnChangeSynth)

        # Translators: This is the label for the button used to change synthesizer,
        # it appears in the context of a synthesizer group on the speech settings panel.
        changeSynthBtn = wx.Button(synthBox, label=_("C&hange..."))
        # self.bindHelpEvent("SpeechSettingsChange", self.synthNameCtrl)
        # self.bindHelpEvent("SpeechSettingsChange", changeSynthBtn)
        synthGroup.addItem(
            guiHelper.associateElements(
                self.synthNameCtrl,
                changeSynthBtn
            )
        )
        changeSynthBtn.Bind(wx.EVT_BUTTON,self.onChangeSynth)

        self.voicePanel = VoiceSettingsPanel(self)
        settingsSizerHelper.addItem(self.voicePanel)

    def _enterTriggersOnChangeSynth(self, evt):
        # log.info("_enterTriggersOnChangeSynth")
        if evt.KeyCode == wx.WXK_RETURN:
            self.onChangeSynth(evt)
        else:
            evt.Skip()

    def onChangeSynth(self, evt):
        changeSynth = SynthesizerSelectionDialog(self, multiInstanceAllowed=True)
        ret = changeSynth.ShowModal()
        if ret == wx.ID_OK:
            self.Freeze()
            # trigger a refresh of the settings
            self.postInit()
            # self._sendLayoutUpdatedEvent()
            self.Thaw()

    def updateCurrentSynth(self):
        synthDesc = get_eng_synth_desc()
        self.synthNameCtrl.SetValue(synthDesc)

    def postInit(self):
        self.voicePanel.onPanelActivated()
        self.synthNameCtrl.SetFocus()

    def onCancel(self, evt):
        # log.info("onCancel")
        self.voicePanel.onDiscard()
        super(EnglishSpeechSettingsDialog, self).onCancel(evt)

    def onOk(self, evt):
        # log.info("onOk")
        self.voicePanel.onSave()
        super(EnglishSpeechSettingsDialog, self).onOk(evt)

    def onClose(self, evt):
        # log.info("onClose")
        self.voicePanel.onDiscard()
        super(EnglishSpeechSettingsDialog, self).onClose(evt)

class SynthesizerSelectionDialog(SettingsDialog):
    """Used to let users set the English synthesizer. Modified from 
    gui.settingsDialogs. Works in a very similar fashion. 
    """
    # Translators: This is the label for the synthesizer selection dialog
    title = _("Select Synthesizer")
    helpId = "SynthesizerSelection"
    synthNames = []

    def makeSettings(self, settingsSizer):
        settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        # Translators: This is a label for the select
        # synthesizer combobox in the synthesizer dialog.
        synthListLabelText=_("&Synthesizer:")
        self.synthList = settingsSizerHelper.addLabeledControl(synthListLabelText, wx.Choice, choices=[])
        self.updateSynthesizerList()
        self.synthList.Bind(wx.EVT_CHOICE, self.onSynthSelected)

    def postInit(self):
        # Finally, ensure that focus is on the synthlist
        self.synthList.SetFocus()

    def updateSynthesizerList(self):
        driverList=self.get_eng_synths()
        self.synthNames=[x[0] for x in driverList]
        options=[x[1] for x in driverList]
        self.synthList.Clear()
        self.synthList.AppendItems(options)
        try:
            index=self.synthNames.index(get_eng_synth_name())
            self.synthList.SetSelection(index)
        except:
            pass

    def get_eng_synths(self):
        """Generates the list of available synthesizers with English voices
        """
        eng_synth_list=[]
        gui.mainFrame.prePopup()
        # Show progress dialog
        progressDialog = wx.ProgressDialog("Updating English TTS List",
                        "Please wait...",
                        maximum=100, parent=self,
                        style=wx.PD_CAN_ABORT | wx.PD_AUTO_HIDE)
        progressDialog.Raise()

        synths  = getSynthList()
        # hack to get the ceiling of the division
        increment = -(-100 // len(synths))

        # log.info(f"got synths: f{synths}")
        for synthName, synthDesc in synths:
            # don't check Hear2Read and sapi5 voices
            # log.info(f"checking synth: {synthName}")
            if ("Hear2Read" in synthName or "dual_sapi5" in synthName or 
                "MultiLang" in synthName or get_eng_synth_name() in synthName):
                continue
            
            try:
                synth = getSynthInstance(synthName)
                voices = synth._get_availableVoices()
            except Exception as e:
                log.warn(f"Unable to list voices from \"{synthName}\", skipping: {e}")
                try:
                    synth.cancel()
                    synth.terminate()
                except:
                    log.warn("Unable to kill synth, skipping")
                    pass
                continue
            eng_voices = []
            for voice in voices.values():
                # log.info(f"checking voice: {voice}")
                # log.info(f"checking voice: {voice.language}, {voice.id}")
                if ((voice.language and voice.language.startswith("en")) 
                    or (not voice.language 
                        and "english" in voice.displayName.lower())):
                    # log.info(f"adding voice: {voice.displayName}")
                    eng_voices.append(voice)
                    break
            if eng_voices:
                eng_synth_list.append((synthName, synthDesc))
                # eng_voices[synthName] = eng_voices
            synth.cancel()
            synth.terminate()
            if progressDialog:
                # check increment against 100 as ceiling can exceed 100
                continue_update,skip = progressDialog.Update(min(increment, 100))
                increment += increment
                if not continue_update:
                    # log.info("not continuing synth update on cancel")
                    eng_synth_list.append((get_eng_synth_name(), get_eng_synth_desc()))
                    eng_synth_list.sort(key=lambda s: strxfrm(s[1]))
                    try:
                        progressDialog.Destroy()
                        del progressDialog
                    except:
                        # log.info("Eng Synth progress dialog not present, skipping")
                        pass
                    return eng_synth_list

        eng_synth_list.append((get_eng_synth_name(), get_eng_synth_desc()))
        eng_synth_list.sort(key=lambda s: strxfrm(s[1]))
        
        progressDialog.Destroy()
        del progressDialog
        gui.mainFrame.postPopup()

        return eng_synth_list
    
    def onSynthSelected(self, evt):        
        # log.info(f"setting synth {self.synthList.GetString(self.synthList.GetSelection())}")
        set_eng_synth(self.synthNames[self.synthList.GetSelection()])

    def onOk(self, evt):
        if not self.synthNames:
            # The list of synths has not been populated yet, so we didn't change anything in this panel
            return

        # config.conf["speech"]["outputDevice"]=self.deviceList.GetStringSelection()
        newSynth=self.synthNames[self.synthList.GetSelection()]
        if not set_eng_synth(newSynth):
            # TODO ensure valid synth is set?
            # Translators: This message is presented when NVDA is unable to load the selected
            # synthesizer.
            gui.messageBox(_("Could not load the %s synthesizer.")%newSynth,
                           # Translators: Title of message box shown on synthesizer load error
                           _("Synthesizer Error"),wx.OK|wx.ICON_WARNING,self)
            return
        # if audioDucking.isAudioDuckingSupported():
        #     index=self.duckingList.GetSelection()
        #     config.conf['audio']['audioDuckingMode']=index
        #     audioDucking.setAudioDuckingMode(index)

        # Reinitialize the tones module to update the audio device
        import tones
        tones.terminate()
        tones.initialize()

        if self.IsModal():
            # Hack: we need to update the synth in our parent window before closing.
            # Otherwise, NVDA will report the old synth even though the new synth is reflected visually.
            self.Parent.updateCurrentSynth()
        super(SynthesizerSelectionDialog, self).onOk(evt)

class VoiceSettingsPanel(SettingsPanel):
    """Used to set the English voice settings. Modified from
    gui.settingsDialogs. Goes into the  EnglishSpeechSettingsDialog. Settings 
    are automatically populated depending on what settings are provided by 
    the selected English synthesizer, from the list of ['rate', 'pitch', 
    'inflection', 'volume']
    """
    # Translators: This is the label for the voice settings panel.
    title = _("Voice")
    helpId = "SpeechSettings"
    synth = None

    # def __init__(self, *args, **kwargs):
    def __init__(self, parent: wx.Window):
        # super(VoiceSettingsPanel, self).__init__(*args, **kwargs)
        # TODO: remove?
        # because settings instances can be of type L{Driver} as well, we have to handle
        # showing settings for non-instances. Because of this, we must reacquire a reference
        # to the settings class whenever we wish to use it (via L{getSettings}) in case the instance changes.
        # We also use the weakref to refresh the gui when an instance dies.
        # log.info("Hear2Read")
        self._currentEngSynthRef = weakref.ref(
            get_eng_synth(),
            lambda ref: wx.CallAfter(self.refreshGui)
        )
        super().__init__(parent)

    def makeSettings(self, settingsSizer):
        # Construct synthesizer settings

        settingsSizerHelper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
        # Translators: This is the label for a combobox in the
        # voice settings panel (possible choices are none, some, most and all).
        voiceLabelText = _("English Voices:")

        eng_voices = get_eng_synth_voicelist()
        # log.info(f"got eng voices: {eng_voices}")
        # eng_voices =  OrderedDict(
        #     (voice_id, info)
        #     for voice_id, info in all_voices.items()
        #     if getattr(info, "language", "").lower().startswith("en")
        # )

        self.eng_voice_descs = []
        self.eng_voice_ids = []
        for id, voice_info in eng_voices.items():
            self.eng_voice_descs.append(voice_info.displayName)
            self.eng_voice_ids.append(id)

        self.voiceList = settingsSizerHelper.addLabeledControl(
            voiceLabelText, wx.Choice, choices=self.eng_voice_descs
        )
        curVoice = get_eng_synth_voice()
        # confVoice = config.conf.get("hear2read", {}).get("engVoice", "")
        # TODO check if no voice available
        # log.info(f"trying to get {curVoice} from list {self.eng_voice_ids}")
        self.voiceList.SetSelection(
            self.eng_voice_ids.index(curVoice)
        )
        self.voiceList.Bind(wx.EVT_CHOICE, self.onEngVoiceChange)

        supportedSettings = get_eng_synth().supportedSettings

        settingsDict = {setting.id: setting for setting in supportedSettings}

        # log.info(f"supported settings: {[i.id for i in supportedSettings]}")

        sliderSettings = ['rate', 'pitch', 'inflection', 'volume']

        self.lastControl = self.voiceList

        id = "variant"
        if id in settingsDict.keys():
            s = self._makeStringSettingControl(settingsDict[id],
                                               get_eng_synth())
            self.settingsSizer.Add(
                s,
                border = 10
            )

        for id in sliderSettings:
            if id in settingsDict.keys():
                s = self._makeSliderSettingControl(settingsDict[id], 
                                               get_eng_synth())
                
                self.settingsSizer.Add(
                    s,
                    border=10
                    # flag=wx.BOTTOM
                )

        self.settingsSizer.Layout()
        # settingsSizer.Layout()

    def _makeSliderSettingControl(
            self,
            setting: NumericDriverSetting,
            settingsStorage: Any
    ) -> wx.BoxSizer:
        """Constructs appropriate GUI controls for given L{DriverSetting} such as label and slider.
        @param setting: Setting to construct controls for
        @param settingsStorage: where to get initial values / set values.
            This param must have an attribute with a name matching setting.id.
            In most cases it will be of type L{AutoSettings}
        @return: wx.BoxSizer containing newly created controls.
        """
        labeledControl = guiHelper.LabeledControlHelper(
            self,
            f"{setting.displayNameWithAccelerator}:",
            nvdaControls.EnhancedInputSlider,
            minValue=setting.minVal,
            maxValue=setting.maxVal
        )
        lSlider=labeledControl.control
        setattr(self, f"{setting.id}Slider", lSlider)
        # TODO duplicate code to avoid breaking changes in the future?
        lSlider.Bind(wx.EVT_SLIDER, DriverSettingChanger(
            settingsStorage, setting
        ))
        # self._setSliderStepSizes(lSlider, setting)
        lSlider.SetLineSize(setting.minStep)
        lSlider.SetPageSize(setting.largeStep)
        lSlider.SetValue(getattr(settingsStorage, setting.id))
        if self.lastControl:
            lSlider.MoveAfterInTabOrder(self.lastControl)
        self.lastControl=lSlider
        return labeledControl.sizer

    def _makeStringSettingControl(
            self,
            setting: DriverSetting,
            settingsStorage: Any
    ):
        """
        Same as L{_makeSliderSettingControl} but for string settings displayed in a wx.Choice control
        Options for the choice control come from the availableXstringvalues property
        (Dict[id, StringParameterInfo]) on the instance returned by self.getSettings()
        The id of the value is stored on settingsStorage.
        Returns sizer with label and combobox.
        """
        labelText = f"{setting.displayNameWithAccelerator}:"
        stringSettingAttribName = f"_{setting.id}s"
        setattr(
            self,
            stringSettingAttribName,
            # Settings are stored as an ordered dict.
            # Therefore wrap this inside a list call.
            list(getattr(
                # self.getSettings(),
                get_eng_synth(),
                f"available{setting.id.capitalize()}s"
            ).values())
        )
        stringSettings = getattr(self, stringSettingAttribName)
        labeledControl = guiHelper.LabeledControlHelper(
            self,
            labelText,
            wx.Choice,
            choices=[x.displayName for x in stringSettings]
        )
        lCombo = labeledControl.control
        setattr(self, f"{setting.id}List", lCombo)
        # self.bindHelpEvent(
        #     self._getSettingControlHelpId(setting.id),
        #     lCombo
        # )

        try:
            cur = getattr(settingsStorage, setting.id)
            selectionIndex = [
                x.id for x in stringSettings
            ].index(cur)
            lCombo.SetSelection(selectionIndex)
        except ValueError:
            pass
        lCombo.Bind(
            wx.EVT_CHOICE,
            StringDriverSettingChanger(settingsStorage, setting, self)
        )
        if self.lastControl:
            lCombo.MoveAfterInTabOrder(self.lastControl)
        self.lastControl = lCombo
        return labeledControl.sizer

    def onEngVoiceChange(self, event):
        # log.info(f"setting voice {self.voiceList.GetString(self.voiceList.GetSelection())}")
        set_eng_synth_voice(self.eng_voice_ids[self.voiceList.GetSelection()])
    

    def refreshGui(self):
        #TODO not adding volume visually when switched from oneCore to
        # log.info("refreshGui")
        if not self._currentEngSynthRef() or self._currentEngSynthRef() is not get_eng_synth():
            # log.info("refreshGui refreshing panel")
            self.settingsSizer.Clear(delete_windows=True)
            self._currentEngSynthRef = weakref.ref(
                get_eng_synth(),
                lambda ref: wx.CallAfter(self.refreshGui)
            )
            self.makeSettings(self.settingsSizer)

    def onPanelActivated(self):
        """Called after the panel has been activated
        """
        # log.info("onPanelActivated")
        self.refreshGui()
        super().onPanelActivated()

    def onDiscard(self):
        # log.info("onDiscard")
        if _h2r_config[SCT_EngSynth][ID_EnglishSynthName] != get_eng_synth_name():
            set_eng_synth(_h2r_config[SCT_EngSynth][ID_EnglishSynthName])
        conf_eng_voice = _h2r_config[SCT_EngSynth][ID_EnglishSynthVoice]
        conf_eng_variant = _h2r_config[SCT_EngSynth][ID_EnglishSynthVariant]
        # need to make sure that the English voice exists in config as default
        # value is an empty string
        if conf_eng_voice and conf_eng_voice != get_eng_synth_voice():
            set_eng_synth_voice(_h2r_config[SCT_EngSynth][ID_EnglishSynthVoice])
        if conf_eng_variant and conf_eng_variant != get_eng_synth_variant():
            set_eng_synth_variant(_h2r_config[SCT_EngSynth][ID_EnglishSynthVariant])
        if _h2r_config[SCT_EngSynth][ID_EnglishSynthRate] != get_eng_synth_rate():
            set_eng_synth_rate(_h2r_config[SCT_EngSynth][ID_EnglishSynthRate])
        if _h2r_config[SCT_EngSynth][ID_EnglishSynthPitch] != get_eng_synth_pitch():
            set_eng_synth_pitch(_h2r_config[SCT_EngSynth][ID_EnglishSynthPitch])
        if _h2r_config[SCT_EngSynth][ID_EnglishSynthVolume] != get_eng_synth_volume():
            set_eng_synth_volume(_h2r_config[SCT_EngSynth][ID_EnglishSynthVolume])
        if _h2r_config[SCT_EngSynth][ID_EnglishSynthVolume] != get_eng_synth_inflection():
            set_eng_synth_inflection(_h2r_config[SCT_EngSynth][ID_EnglishSynthInflection])

    def onSave(self):
        # log.info("onSave")
        _h2r_config[SCT_EngSynth][ID_EnglishSynthName] = get_eng_synth_name()
        _h2r_config[SCT_EngSynth][ID_EnglishSynthVoice] = get_eng_synth_voice()
        _h2r_config[SCT_EngSynth][ID_EnglishSynthVariant] = get_eng_synth_variant()
        _h2r_config[SCT_EngSynth][ID_EnglishSynthRate] = get_eng_synth_rate()
        _h2r_config[SCT_EngSynth][ID_EnglishSynthPitch] = get_eng_synth_pitch()
        _h2r_config[SCT_EngSynth][ID_EnglishSynthVolume] = get_eng_synth_volume()
        _h2r_config[SCT_EngSynth][ID_EnglishSynthInflection] = get_eng_synth_inflection()
        config.conf.save()

