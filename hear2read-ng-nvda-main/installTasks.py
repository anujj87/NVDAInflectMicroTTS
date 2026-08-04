# A part of the Hear2Read Indic Voices addon for NVDA
# Copyright (C) 2013-2024, Hear2Read Project Contributors
# See the file COPYING for more details.

import os
import shutil
import sys
from pathlib import Path

import gui
import wx
from logHandler import log

# repeating path variable initializations as importing from modules is not 
# allowed in installTasks
APPDATA = Path(os.getenv('APPDATA') or Path.home())
H2RNG_DATA_DIR = APPDATA / "Hear2Read-NG"
H2RNG_DLL_NAME = "h2r-ng.dll"
H2RNG_ENGINE_DLL_PATH = H2RNG_DATA_DIR / H2RNG_DLL_NAME
# temporary path to update DLL without needing current Hear2ReadNG engine to stop (release DLL)
H2RNG_ENGINE_UPDATE_PATH = H2RNG_DATA_DIR / f"{H2RNG_DLL_NAME}.update"
H2RNG_VOICES_DIR = H2RNG_DATA_DIR / "Voices"
H2RNG_WAVS_DIR = H2RNG_DATA_DIR / "wavs"
H2RNG_UPDATE_FLAG = H2RNG_DATA_DIR / "pendingUpdate"

LOG_TAG = "Hear2ReadNG-installTasks"

try:
    _diros=os.path.dirname(__file__.decode("mbcs"))
except AttributeError:
    _diros=os.path.dirname(__file__)

_dir = Path(_diros)
    
OLD_H2RNG_DATA_DIR = os.path.join(os.environ['ALLUSERSPROFILE'], 
                                  "Hear2Read-ng")

# TODO: Obsolete, remove
def move_old_voices():
    """Tries to move voices downloaded in addon version 1.4 and lower to the 
    new dir structure to be usable by this addon.
    """
    old_voices_dir = os.path.join(OLD_H2RNG_DATA_DIR, "Voices")

    if os.path.isdir(OLD_H2RNG_DATA_DIR):
        # try moving old voices
        for file in os.listdir(old_voices_dir):
            try:
                src_path = os.path.join(old_voices_dir, file)
                dst_path = os.path.join(H2RNG_VOICES_DIR, file)
                if not file.startswith("en") and not os.path.isfile(dst_path):
                    shutil.copy2(src=src_path, dst=dst_path)
                os.remove(src_path)
            except Exception as e:
                log.warn(f"{LOG_TAG}: unable to remove old voice file: {file}, {e}")
        
        old_wavs_dir = os.path.join(OLD_H2RNG_DATA_DIR, "wavs")

        if os.path.isdir(old_wavs_dir):
            try:
                copytree_overwrite(src=old_wavs_dir, dst=H2RNG_WAVS_DIR)
            except Exception as e:
                log.warn(f"{LOG_TAG}: unable to copy old wav folders: {e}")

        # try deleting old data
        old_dirs = []
        old_dirs.append(old_voices_dir)
        old_dirs.append(old_wavs_dir)
        old_dirs.append(os.path.join(OLD_H2RNG_DATA_DIR, "espeak-ng-data"))

        for dir in old_dirs:
            try:
                if os.path.isdir(dir):
                    shutil.rmtree(dir)
            except Exception as e:
                log.warn(f"{LOG_TAG}: unable to remove old folder: {dir}, {e}")

        try:
            shutil.rmtree(OLD_H2RNG_DATA_DIR)
        except Exception as e:
            log.warn(f"{LOG_TAG}: unable to remove old Hear2Read data folder: {e}")

# TODO: Obsolete, remove
def copytree_compat(src, dst):
    """Copytree version with overwrite compatible for Python < 3.8. This is
    copied from the answer https://stackoverflow.com/a/13814557, and has a 
    fairly basic functionality not accounting for symlinks, which is sufficient
    for our purposes

    @param src: path to the source, to be copied from
    @type src: string
    @param dst: path to the destination, to be copied to
    @type dst: string
    """
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            copytree_compat(s, d)
        else:
            if not os.path.exists(d) or os.stat(s).st_mtime - os.stat(d).st_mtime > 1:
                shutil.copy2(s, d)

def copytree_overwrite(src, dst):
    """Wrapper to enable consistent behaviour in Python version < 3.8

    @param src: path to the source, to be copied from
    @type src: string
    @param dst: path to the destination, to be copied to
    @type dst: string
    """
    if sys.version_info >= (3, 8):
        shutil.copytree(src=src, dst=dst, dirs_exist_ok=True)
    else:
        copytree_compat(src=src, dst=dst)
                
def onInstall():
    """Copies essential Hear2Read files to the designated data folder, then
    attempts to move data from older installs to this folder.
    """
    src_dir = _dir / "res"
    src_dll = src_dir / H2RNG_DLL_NAME

    # First check if update, i.e., H2RNG_DATA_DIR exists.
    if H2RNG_DATA_DIR.is_dir():            
        # if the data dir is already present, need to take further steps:
        # touch a file called update flag. This is to ensure proper update 
        # behaviour in NVDA - NVDA runs onUninstall when updating, deleting
        # old voices
        with open(H2RNG_UPDATE_FLAG, 'a'):
            os.utime(H2RNG_UPDATE_FLAG, None)
        try:
            # trying moving the dll first
            # log.info(f"{LOG_TAG}: Trying to move dll: {src_dir / H2RNG_DLL_NAME} -> {H2RNG_DATA_DIR / H2RNG_DLL_NAME}")
            shutil.move(src_dll, H2RNG_ENGINE_UPDATE_PATH)
            if src_dll.exists() and H2RNG_ENGINE_UPDATE_PATH.exists():
                try:
                    src_dll.unlink()
                except Exception as e:
                    log.warn(f"{LOG_TAG}: Unable to delete dll from res, might create issues: {e}")
        except Exception as e:
            log.warn(f"{LOG_TAG}: Ran into error moving files onInstall: {e}")
            if H2RNG_DLL_NAME in str(e) and not H2RNG_ENGINE_UPDATE_PATH.exists():
                log.error(f"{LOG_TAG}: Unable to install engine. Install will probably fail")
            else:
                log.warn(f"{LOG_TAG}: Unable to update Hear2Read properly. Old voices may be "
                         "deleted")
    try:
        copytree_overwrite(src=src_dir, dst=H2RNG_DATA_DIR)
        shutil.rmtree(src_dir)
    except Exception as e:
        log.warn(f"{LOG_TAG}: Error installing Hear2ReadNG data files: {e}")
        if H2RNG_DLL_NAME in str(e):
            # if the dll has been copied successfully, we can ignore this, as we will clean up on
            # restart
            if H2RNG_ENGINE_UPDATE_PATH.exists():
                pass
            else:
                gui.messageBox(
                    # Translators: message telling the user that Hear2Read Indic was not installed correctly
                    _("Unable to update Hear2ReadNG while it is running in NVDA\n"
                        "Please switch to a different synthesizer, restart NVDA and retry"),
                    # Translators: title of a message telling the user that Hear2Read Indic was not installed correctly
                    _("Hear2ReadNG Install Error"),
                    wx.OK | wx.ICON_ERROR)
                raise e

    src_voice_dir = src_dir / "Voices"
    if src_voice_dir.is_dir():
        for file in os.listdir(src_voice_dir):
            try:
                os.remove(src_voice_dir / file)
            except Exception as e:
                log.warn(f"{LOG_TAG}: unable to remove file from addon dir: {file}, {e}")

    move_old_voices()

    # We have renamed the addon to conform with the rule of having no spaces
    # We will try to remove the older addon
    old_addon_dir = _dir.parent / "Hear2Read NG"
    if old_addon_dir.is_dir():
        log.info(f"{LOG_TAG}: Found older version of Hear2ReadNG, removing the addon")
        try:
            shutil.rmtree(old_addon_dir)
        except:
            log.warn(f"{LOG_TAG}: Unable to remove the old addon. Please remove manually")

def onUninstall():
    log.info(f"{LOG_TAG}: uninstalling...")
    if H2RNG_UPDATE_FLAG.exists():
            os.remove(H2RNG_UPDATE_FLAG)

    if H2RNG_ENGINE_UPDATE_PATH.exists():
        # remove the update flag file so uninstall has the desired effect
        # subsequently
        log.info(f"{LOG_TAG}: Addon update. Ignoring uninstall tasks")
        try:
            # log.info("Hear2Read update from onUninstall")
            shutil.move(H2RNG_ENGINE_UPDATE_PATH, H2RNG_ENGINE_DLL_PATH)
        except Exception as e:
            log.error(f"{LOG_TAG}: Unable to install Hear2ReadNG TTS Engine! {e}")
        return
    try:
        shutil.rmtree(H2RNG_DATA_DIR)
    except Exception as e:
        log.warn(f"{LOG_TAG}: Error removing Hear2ReadNG files on uninstall: {e}")