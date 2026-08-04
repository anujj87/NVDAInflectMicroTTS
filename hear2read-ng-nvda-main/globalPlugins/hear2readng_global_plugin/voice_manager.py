# A part of the Hear2Read Indic Voices addon for NVDA
# Copyright (C) 2013-2024, Hear2Read Project Contributors
# See the file COPYING for more details.

import operator
import os
import zipfile
from threading import Event, Thread

import gui
import requests
import synthDriverHandler
import winUser
import wx
from addonHandler import getCodeAddon

# from gui.addonGui import promptUserForRestart
from logHandler import log
from requests.exceptions import HTTPError
from systemUtils import ExecAndPump

from synthDrivers._H2R_NG_Speak import H2RNG_DATA_DIR, H2RNG_VOICES_DIR

from .h2rutils import (
    H2RNG_VOICES_DOWNLOAD_HTTP,
    Voice,
    check_files,
    fetch_server_voices,
    onInstall,
    populateVoices,
)

# Constants and global variables:

DLL_FILE_NAME_PREFIX = "h2r-ng"
DOWNLOAD_SUFFIX = ".download"


class Hear2ReadNGVoiceManagerDialog(wx.Dialog):
    def __init__(self, parent=gui.mainFrame, title="Hear2Read Indic Voice Manager"):
        """Constructor for the main window of the voice download manager. 
        Performs installation checks and initializes class attributes. Also 
        populates the list of voices to be displayed in the manager.
        """
        super().__init__(parent, title=title)
        # Used to prompt restart post closing dialog when voices added/removed
        self.session_voice_modified = False
        self.Bind(wx.EVT_CLOSE, self.onClose)

        # Check the synth files and try one time install if installTasks failed
        if not check_files():
            install_success = False
            try:
                onInstall()
                # recheck after attempting install
                install_success = check_files()
            except Exception as e:
                install_success = False
            
            # Warn user and exit in case installing data failed
            if not install_success:
                gui.messageBox(
                    # Translators: message telling the user that Hear2Read Indic was not installed correctly
                    _("Hear2Read Indic addon not installed properly.\n"
                      "Please reinstall the addon from the file and retry.\n"),
                    # Translators: title of a message telling the user that Hear2Read Indic was not installed correctly
                    _("Hear2Read Indic Error"),
                    wx.OK | wx.ICON_ERROR,)
                self.EndModal(wx.ID_CANCEL)
                # self.Destroy()
                return
            
            # Inform user voices have been transferred and prompt NVDA restart
            if install_success:
                self.session_voice_modified = True
                retval = gui.messageBox(
                    # Translators: content of a message box
                    _("Successfully moved voices downloaded in previous"
                        " version.\n"
                        "To use these voices, you need to restart NVDA.\n"
                        "Do you want to restart NVDA now?"),
                    # Translators: title of a message box
                    _("Voices installed"),
                        wx.YES_NO | wx.ICON_WARNING,
                    )
                if retval == wx.YES:
                    # set_voice_install_restart(True)
                    # core.restart()
                    self.EndModal(wx.ID_SETUP)
                    # self.Destroy()
                    return

        # initialize attributes:

        # list of Voice objects of voices on the server
        self.server_voices = {}
        # list of Voice objects of voices installed
        self.installed_voices = {}
        # dictionary of Voice objects of voices that have updates, keyed by 
        # ISO 2 codes of the corresponding languages
        self.update_langs = {}
        # list of Voice objects of Voices to be displayed in the window
        self.display_voices = []
        # event set on network error
        self.network_error_event = Event()
        # event set on network error
        self.server_error_event = Event()

        try:
            version = getCodeAddon().manifest.version
        except:
            log.warn("Hear2Read NG: Unable to read manifest, assuming default version number")
            version  = "1.7.3"

        version_split = version.split(".")
        self.major_version = int(version_split[0])
        self.minor_version = int(version_split[1])

        self.get_display_voices()

        if not self.display_voices:
            gui.messageBox(
                # Translators: message telling the user that no voices are installed and no internet connection
                _("No Hear2Read Indic voices installed and \n"
                  "unable to connect to the internet \n"
                  "Please check internet connection and retry "),
                # Translators: title of a message telling the user that Hear2Read Indic was not installed correctly
                _("Hear2Read Indic No Voices"),
                wx.OK | wx.ICON_ERROR,)
            self.Destroy()
            return

        self.setup_display()


    def setup_display(self):
        """Helper function that initializes the display elements
        """
        self.SetFont(wx.Font(12, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_NORMAL))

        self.vbox = wx.BoxSizer(wx.VERTICAL)
        self.vboxHelper = gui.guiHelper.BoxSizerHelper(self, wx.VERTICAL)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # TODO no logo for now
        # Logo button
        # logo_button = wx.Button(self.title_panel, label="", size=(216, 48),
        #                          style=wx.NO_BORDER)
        # logo_button.SetBackgroundColour("#b3c6ff")

        # logo_bitmap = wx.StaticBitmap(logo_button,
        #                                bitmap=wx.Bitmap(
        #                                    "hear2read-horizontal@2x.png"))
        # logo_button.Bind(wx.EVT_BUTTON, self.on_logo_click)
        
        self.title_text = wx.StaticText(self, label="Hear2Read Voice Manager")
        self.title_text.SetFont(wx.Font(22, wx.FONTFAMILY_DEFAULT,
                                    wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.title_text.SetForegroundColour("#FF101FBB")
        self.title_text.SetBackgroundColour("#b3c6ff")
        top_sizer.Add(self.title_text, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        # top_sizer.AddStretchSpacer()
        # top_sizer.Add(logo_button, 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL, 0)

        self.vboxHelper.addItem(top_sizer)
        
        # Set a larger font
        font = self.GetFont()
        font.SetPointSize(int(font.GetPointSize() * 1.25))

        self.list_ctrl = self.vboxHelper.addLabeledControl(
                        "Hear2Read Voice List", wx.ListCtrl, 
                        style=wx.LC_REPORT | wx.BORDER_NONE | wx.LC_NO_HEADER)

        self.list_ctrl.InsertColumn(0, 'Voice')#, width=140)
        self.list_ctrl.InsertColumn(1, 'Action')#, width=80)
        self.list_ctrl.SetFont(font=font)
                
        self.display_voice_list()


    def display_voice_list(self):
        """Helper function to populate the UI ListCtrl of the voices with the 
        appropriate on click functionality
        """
        # Add voices to the list
        for voice in self.display_voices:
            if voice.lang_iso in self.update_langs.keys():
                self.list_ctrl.Append([voice.display_name, "Update"])
            else:
                self.list_ctrl.Append([voice.display_name, voice.state])

        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_click_item)

        if self.display_voices:
            self.list_ctrl.SetColumnWidth(0, wx.LIST_AUTOSIZE)
            self.list_ctrl.SetColumnWidth(1, wx.LIST_AUTOSIZE)

        if self.list_ctrl.GetColumnWidth(1) < 130:
            self.list_ctrl.SetColumnWidth(0, 145)
            self.list_ctrl.SetColumnWidth(1, 135)

        self.list_ctrl.SetMinSize((self.title_text.GetSize().GetWidth(), -1))

        self.vbox.Add(self.vboxHelper.sizer, border=10, flag=wx.ALL)
        self.vbox.Fit(self)
        self.SetSizer(self.vbox)
        # TODO check if redundant
        self.vbox.Fit(self)

        self.Fit()
        self.Layout()
        
        # on the event of server error, warn the user and inform that only 
        # voices already installed are being shown
        if self.server_error_event.is_set():
            # Translators: Message of dialog when an error occurs.
            gui.messageBox(_("Failed to connect to server " 
                            "\nPlease contact us for assistance at feedback@hear2read.org "
                             "\nWe will only display installed voices "),
                            # Translators: The title of a dialog presented when an error occurs.
                            _("Network Error"),
                            wx.OK | wx.ICON_WARNING)
            
        # on the event of network error, warn the user and inform that only 
        # voices already installed are being shown
        elif self.network_error_event.is_set():
            # Translators: Message of dialog when an error occurs.
            gui.messageBox(_("Failed to connect to the internet " 
                            "\nPlease check your internet connection "
                             "\nWe will only display installed voices "),
                            # Translators: The title of a dialog presented when an error occurs.
                            _("Network Error"),
                            wx.OK | wx.ICON_WARNING)


    def delete_voice_files(self, voice):
        """Function to delete voice files associated with the voice

        @param voice: the Voice object to be removed
        @type voice: utils.Voice
        """
        # TODO remove wav files as well
        voice_files = H2RNG_VOICES_DIR.glob(f"{voice.id}*")

        for f in voice_files:
            f.unlink()

    def on_click_item(self, event):
        """On click event handler for the items in the list of voices. Depending
        on the state of the voice, it is possible to perform one of three 
        actions on the voice: dowload, update or remove 

        @param event: the event passed by the wx gui
        """
        index = event.GetIndex()
        voice = self.display_voices[index]
        action = self.list_ctrl.GetItem(index, 1).GetText()

        if action == "Download":
            self.startDownload(voice=voice, index=index)
        elif action == "Update":
            old_voice = self.update_langs.get(voice.lang_iso)
            self.startUpdate(voice=voice, index=index, old_voice=old_voice)
        else:
            self.remove_voice(voice, index)

    def startDownload(self, voice, index, old_voice=None):
        """Function to download or update voice. If updating, pass the old voice.

        @param voice: the Voice object of the voice to be downloaded
        @type voice: utils.Voice
        @param index: index of list item of voice in manager
        @type index: int
        @param old_voice: the Voice object of the voice to be updated, defaults to None
        @type old_voice: utils.Voice, optional
        """
        if not H2RNG_VOICES_DIR.is_dir():
            try:
                os.makedirs(H2RNG_VOICES_DIR)
            except:
                return gui.messageBox(
                    # Translators: The message displayed if the folder to store the downloaded file can't be created.
                    _("Unable to create voice files directory."),
                    # Translators: The title displayed if the folder to store the downloaded file can't be created.
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                    self
                )
        # dest = path.join(self.storeUpdatesDir, updateInfo['name'])
        if self.guiDownloadVoiceFiles(voice):
            if self.guiInstallVoice(
                    voice,
                    old_voice
                    ):
                self.list_ctrl.SetItem(index, 1, "Remove")
                self.session_voice_modified = True
                
                retval = gui.messageBox(
                    # Translators: content of a message box
                    _(
                        f"Successfully downloaded {voice.display_name} voice.\n"
                        "To use this voice, you need to restart NVDA.\n"
                        "Do you want to restart NVDA now?"
                    ),
                    # Translators: title of a message box
                    _("Voice installed"),
                        wx.YES_NO | wx.ICON_WARNING,
                    )
                
                if retval == wx.YES:
                    # core.restart()
                    self.EndModal(wx.ID_SETUP)
                    # self.Destroy()
                    # promptUserForRestart()
            else:
                gui.messageBox(
                # Translators: The message displayed when errors were found while trying to install voice.
                    _(f"Error installing {voice.display_name} voice"),
                # Translators: Title of error message box
                    _("Error"), wx.OK|wx.ICON_ERROR, 
                    self)
        else:
            # Translators: The message displayed when errors were found while trying to download voice.
            gui.messageBox(
                _(f"Error downloading {voice.display_name} voice"),
                # Translators: Title of error message box
                _("Error"),
                wx.OK|wx.ICON_ERROR, 
                self)

    def startUpdate(self, voice, index, old_voice):
        """Function to update voice. Wraps around startDownload with an additional check if voice to
        be updated is in use by TTS.

        @param voice: the Voice object of the voice to be downloaded
        @type voice: utils.Voice
        @param index: index of list item of voice in manager
        @type index: int
        @param old_voice: the Voice object of the voice to be updated
        @type old_voice: utils.Voice
        """
        # first check that the current voice is not the one being removed
        # TODO: this is not a breaking change, check if necessary
        curr_synth = synthDriverHandler.getSynth()

        if ("Hear2Read Indic" in curr_synth.name and 
            (curr_synth.voice == old_voice.id)):
            gui.messageBox(
                # Translators: message in a message box
                _("Cannot update currently active voice!\n"
                  "Change synthesizer or voice to proceed"),
                # Translators: title of a message box
                _("Error"),
                style=wx.ICON_ERROR
            )
            return
        
        self.startDownload(voice=voice, index=index, old_voice=old_voice)


    def guiDownloadVoiceFiles(self, voice):
        """Helper function taking care of the GUI for voice download.

        @param voice: the Voice object of the voice to be downloaded
        @type voice: utils.Voice
        @return: Whether voice download was succesful or not
        @rtype: bool
        """
        # list of tuples of files and respective download URLs
        download_queue = []

        # the main model file
        file = f"{voice.id}.onnx"
        download_url = f"{H2RNG_VOICES_DOWNLOAD_HTTP}{file}"
        # log.info(f"download_voice on: {voice.id}, URL: {download_url}")
        download_queue.append((H2RNG_VOICES_DIR / f"{file}{DOWNLOAD_SUFFIX}", 
                               download_url))
        
        # the model config file
        file_config = f"{file}.json"
        download_url_config = f"{H2RNG_VOICES_DOWNLOAD_HTTP}{file_config}"
        download_queue.append((H2RNG_VOICES_DIR / f"{file_config}{DOWNLOAD_SUFFIX}",  
                               download_url_config))
        
        # the extras file, if present
        if voice.extra:
            file_extra = f"{file}.zip"
            download_url_extra = f"{H2RNG_VOICES_DOWNLOAD_HTTP}{file_extra}"
            download_queue.append((H2RNG_VOICES_DIR / f"{file_extra}{DOWNLOAD_SUFFIX}",  
                                   download_url_extra))
            
        gui.mainFrame.prePopup()
        progressDialog = wx.ProgressDialog(
            "Downloading",
            f"Downloading {voice.display_name}. Please wait...",
            style=wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME | wx.PD_REMAINING_TIME | wx.PD_AUTO_HIDE,
            parent=self)
        # progressDialog.CentreOnScreen()
        progressDialog.Raise()

        def update(val):
            nonlocal progressDialog
            return not progressDialog.Update(val)[0]
            
        res = True
        while True:
            try:
                ExecAndPump(self.download_files, download_queue, update)
                break
            except:
                # Translators: a message dialog asking to retry or cancel when downloading a file.
                message=_("Unable to download file. Perhaps there is no internet access or the server is not responding. Do you want to try again?")
                # Translators: the title of a retry cancel dialog when downloading a file.
                title=_("Error downloading")
                if winUser.MessageBox(None,message,title,winUser.MB_RETRYCANCEL) != winUser.IDRETRY:
                    res=False
                    log.debugWarning(f"Error downloading voice: {voice.display_name}", exc_info=True)
                    break
        if not res:
            try:
                self.delete_voice_files(voice)
            except:
                pass
        progressDialog.Destroy()
        del progressDialog
        gui.mainFrame.postPopup()
        return res

    def download_files(self, download_queue, fnUpdate = None):
        """Handles file downloads. Takes a list of downloads to be done.

        @param download_queue: List of downloads. Each item is a pair of Destination path and URL
        @type List
        @param fnUpdate: Callback to update download progress
        @type Callable[[int], bool]
        """
        for download in download_queue:
            with requests.get(download[1], stream=True) as response:
                response.raise_for_status()
                total_size_header = response.headers.get('content-length')
                total_size = int(total_size_header) if total_size_header else None
                with open(download[0], 'wb') as out_file:
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            out_file.write(chunk)
                            downloaded += len(chunk)

                        if total_size:
                            percent = min(int(downloaded * 100 / total_size), 100)
                            if fnUpdate and fnUpdate(percent):
                                return


    def guiInstallVoice(self, voice, old_voice=None):
        """Helper function taking care of the GUI for voice install post download. Pass old_voice if
        updating.

        @param voice: the Voice object of the voice to be installed
        @type voice: utils.Voice
        @param old_voice: the Voice object of the voice to be updated, defaults to None
        @type old_voice: utils.Voice, optional
        @return: Whether voice install was succesful or not
        @rtype: bool
        """
        gui.mainFrame.prePopup()
        progressDialog = gui.IndeterminateProgressDialog(
                self, 
                "Installing",
                f"Installing {voice.display_name} voice"
                )
        res = True
        while True:
            try:
                ExecAndPump(self.install_voice, voice, old_voice)
                break
            except Exception as e:
                log.warn(f"Error installing voice {voice.display_name}", exc_info=True)
                # Translators: a message dialog asking to retry or cancel when copying files.
                message=_(f"Unable to install {voice.display_name} voice: {e}\n"
                          "Please check if you have low disk space.")
                # Translators: the title of a retry cancel dialog when copying files.
                title=_("Error Copying")
                if winUser.MessageBox(None,message,title,winUser.MB_RETRYCANCEL) != winUser.IDRETRY:
                    res=False
                    log.debugWarning(f"Error installing voice {voice.display_name}", exc_info=True)
                    break
        if not res:
            try:
                self.delete_voice_files(voice)
            except:
                pass
        progressDialog.done()
        del progressDialog
        gui.mainFrame.postPopup()
        return res
    
    def install_voice(self, voice, old_voice=None):
        """Finishes up post download tasks, removing temporary file extensions and extracting voice
        extras

        @param voice: Voice being installed
        @type voice: utils.Voice
        @param old_voice: previous version of the voice in case of update, defaults to None
        @type old_voice: utils.Voice, optional
        """
        def remove_suffix_extract(voice):
            """Does the file handling operations of renaming the files to remove
            the suffix and extracting extras zip file, if present

            @param voice: the Voice object of the voice being installed
            @type voice: utils.Voice
            """
            voice_files = H2RNG_VOICES_DIR.glob(f"{voice.id}*")
            for file in voice_files:
                if file.is_file():
                    # Check if the filename ends with suffix and remove
                    if file.match(f"*{DOWNLOAD_SUFFIX}"):
                        new_file = file.with_suffix("")
                        os.rename(file, new_file)
                        # extract extra files
                        if new_file.match("*.zip"):
                            with zipfile.ZipFile(new_file, 'r') as zipf:
                                zipf.extractall(H2RNG_DATA_DIR)
                            new_file.unlink()

        def remove_old_voice(old_voice):
            """Removes old voice files and the corresponding entry from the
            voice updates dictionary maintained.

            @param old_voice: the Voice object of the voice being removed
            @type old_voice: utils.Voice
            """
            if H2RNG_VOICES_DIR / f"{old_voice.id}.onnx":
                self.delete_voice_files(old_voice)
            self.update_langs.pop(old_voice.lang_iso)

        remove_suffix_extract(voice)
        # Check if updating an older voice, remove old voice
        if old_voice:
            remove_old_voice(old_voice)

    def remove_voice(self, voice, index):
        """Handler for on-click behaviour of removing voice

        @param voice: the Voice object of the voice to be removed
        @type voice: utils.Voice
        """
        # first check that the current voice is not the one being removed
        # TODO: this is not a breaking change, check if necessary
        curr_synth = synthDriverHandler.getSynth()

        if ("Hear2Read Indic" in curr_synth.name and 
            (curr_synth.voice == voice.id)):
            gui.messageBox(
                # Translators: message in a message box
                _("Cannot remove currently active voice!\n"
                  "Change synthesizer or voice to proceed"),
                # Translators: title of a message box
                _("Error"),
                style=wx.ICON_ERROR
            )
            return
        
        confirm_remove = gui.messageBox(
            # Translators: message in a message box
            _("Do you want to remove this voice?\n Voice: "
              f"{voice.display_name}"),
            # Translators: title of a message box
            _("Remove Voice?"),
            style=wx.YES_NO|wx.ICON_WARNING)
        
        if confirm_remove == wx.YES:
            try:
                self.delete_voice_files(voice)
            except:
                log.exception("Failed to remove voice files", exc_info=True)
                gui.messageBox(
                    # Translators: message in a message box
                    _("Failed to remove voice\nSee NVDA's log for more details "),
                    # Translators: title of a message box
                    _("Failed"),
                    style=wx.ICON_WARNING
                )
            else:
                self.session_voice_modified = True
                gui.messageBox(
                    # Translators: message in a message box
                    _("Voice removed successfully."),
                    # Translators: title of a message box
                    _("Done"),
                    style=wx.ICON_INFORMATION
                )
            self.list_ctrl.SetItem(index, 1, "Download")        


    # TODO remove duplicate of utils.populateVoices
    @classmethod  
    def get_installed_voices(cls):
        """Classmethod to get installed voices. Returns a dictionary of voices
        keyed by the ISO code of the language

        @return: Dictionary of installed voices keyed by the 2 letter ISO code 
        of the language
        @rtype: dict
        """
        installed_voices = {}

        if not H2RNG_VOICES_DIR.is_dir():
            return installed_voices

        # clear incomplete downloads -shyam
        for voice_file in H2RNG_VOICES_DIR.glob(f"*.{DOWNLOAD_SUFFIX}"):
            os.remove(voice_file)

        # remove obsolete English voice
        for voice_file in H2RNG_VOICES_DIR.glob("en*"):
            os.remove(voice_file)

        for id, display_name in populateVoices().items():
            if id.startswith("en"):
                continue
            voice_iso = id.split("-")[0].split("_")[0]
            installed_voices[voice_iso] = Voice(id, voice_iso, 
                                                display_name, "Remove")
        
        return installed_voices

    def get_server_voices(self):
        """Populated the list of voices available on the server. Modifies the 
        class attribute server_voices, a list of Voice objects. The operation
        is done in a background thread while a BusyInfo is displayed.
        """
        fetch_complete_event = Event()
        self.server_voices.clear()
        
        loading_dialog = wx.BusyInfo("Fetching voice list... Please wait ", 
                                        parent=self)
        wx.Yield()


        def dismiss_loading_dialog():
            nonlocal loading_dialog
            loading_dialog = None

        def fetch():
            """Main function to fetch the voice list from the server. Sets the
            server_error_event/network_error_event attribute in case of failure 
            """
            try:
                self.server_voices = fetch_server_voices()
            # TODO: these are not going to be reached, remove?
            except HTTPError as http_e:
                self.server_error_event.set()
                log.warn(f"Hear2Read http error: {http_e}")
            except Exception as e:
                self.network_error_event.set()
                log.warn(f"Hear2Read unable to access internet: {e}")
            finally:
                wx.CallAfter(dismiss_loading_dialog)
                fetch_complete_event.set()

        Thread(target=fetch).start()

        fetch_complete_event.wait()
    

    def get_display_voices(self):
        """Compiles the voices to be diplayed from the lists of installed voices
        and voices available online. Modifies the attribute display_voices to
        be a list of Voice objects and sorts it by display_name
        """
        self.installed_voices = self.get_installed_voices()
        self.get_server_voices()

        if not self.server_voices:
            self.display_voices = sorted(list(self.installed_voices.values()),
                                    key=operator.attrgetter("display_name"))
            return
        
        for key in set(self.installed_voices.keys()).union(
                                                    self.server_voices.keys()):
            
            # TODO: this is redundant now as it will be updated post fact. will
            # need a file on the server informing this. Maybe move voices to a
            # new location to prevent access by old versions?
            if key == "sa" and self.major_version > 0 and self.minor_version > 7:
                continue

            local_voice = self.installed_voices.get(key)
            server_voice = self.server_voices.get(key)

            if not local_voice:
                self.display_voices.append(server_voice)
                continue

            if not server_voice:
                self.display_voices.append(local_voice)
                continue

            if local_voice.id != server_voice.id:
                self.display_voices.append(server_voice)
                self.update_langs[local_voice.lang_iso] = local_voice
            else:
                self.display_voices.append(local_voice)

        self.display_voices.sort(key=operator.attrgetter("display_name"))

    def onClose(self, event):
        if self.session_voice_modified:
            self.EndModal(wx.ID_SETUP)
        else:
            self.EndModal(wx.ID_OK)