# -*- coding: UTF-8 -*-
# A part of the Hear2Read Indic Voices addon for NVDA
# Copyright (C) 2013-2024, Hear2Read Project Contributors
# See the file COPYING for more details.

import os
import queue

# import shutil
import threading
from collections import OrderedDict
from ctypes import (
    CFUNCTYPE,
    POINTER,
    Structure,
    c_bool,
    c_char_p,
    c_float,
    c_int,
    c_int16,
    cdll,
    sizeof,
)

import config
import gui
import nvwave
import wx
from logHandler import log
from synthDriverHandler import changeVoice, getSynthInstance

from globalPlugins.hear2readng_global_plugin.file_utils import (
    ADDON_NAME,
    EN_VOICE_ALOK,
    H2RNG_DATA_DIR,
    H2RNG_ENGINE_DLL_PATH,
    H2RNG_VOICES_DIR,
)
from globalPlugins.hear2readng_global_plugin.h2rutils import (
    ID_EnglishSynthInflection,
    ID_EnglishSynthName,
    ID_EnglishSynthPitch,
    ID_EnglishSynthRate,
    ID_EnglishSynthVariant,
    ID_EnglishSynthVoice,
    ID_EnglishSynthVolume,
    SCT_EngSynth,
    _h2r_config,
)

LOG_TAG = f"{ADDON_NAME}-{os.path.basename(__file__).removesuffix(".py")}"

isSpeaking = False
def defaultIndexCallback(idx: int | None):
    return
onIndexReached = defaultIndexCallback
def defaultDoneCallback():
    return
onDone = defaultDoneCallback
bgThread=None
bgQueue = None
player = None
H2RNG_SpeakDLL=None


#error codes
EE_OK=0
EE_INTERNAL_ERROR=-1
EE_BUFFER_FULL=1
EE_NOT_FOUND=2

# offset between ascii and devanagari digits in unicode
DEVANAGARI_DIGIT_OFFSET = 2358

en_voice = EN_VOICE_ALOK

eng_synth = "oneCore"
EngSynth = None

ALOK_ID = 13
DIPAL_ID = 2
AMARPREET_ID = 1
qual_to_hz = {"low":16000, "med":22050}
digit_offsets = {"as": 2486, "ne":2358}

curr_voice = ""
curr_qual = ""

# constants that can be returned by H2R_Speak_callback
CALLBACK_CONTINUE_SYNTHESIS=0
CALLBACK_ABORT_SYNTHESIS=1

def encodeH2RSpeakString(text: str) -> bytes:
    return text.encode('utf8')

def decodeH2RSpeakString(data: bytes) -> str:
    return data.decode('utf8')
    
# callback function decorators
t_H2RNG_audiocallback=CFUNCTYPE(c_int, POINTER(c_int16), c_int)
t_H2RNG_indexcallback=CFUNCTYPE(c_int, c_int)
t_H2RNG_donecallback=CFUNCTYPE(c_int)

class Callbacks(Structure):
    _fields_ = [("ttsAudioCallback", t_H2RNG_audiocallback),
                ("ttsIndexCallback", t_H2RNG_indexcallback),
                ("ttsDoneCallback", t_H2RNG_donecallback)] # added in 2.0
                       
# class SpeechParams(Structure):
#     _fields_ = [("phoneLen", c_float), 
#                 ("volume", c_int), 
#                 ("charMode", c_bool), ]
                
class SpeechParams(Structure):
    _fields_ = [("speed", c_float),
                ("pitch", c_float), 
                ("volume", c_int), 
                ("charMode", c_bool), ]
       
def getCurrentVoice() -> str | None:
    
    if curr_voice:
        return curr_voice
    else:
    # TODO: raise exception?
        # setCurrentVoice(en_voice)
        # return en_voice
        return None
        
def setCurrentVoice(voiceID: str):
    # log.info(f"H2R setCurrentVoice: {voiceID}")
    global curr_voice
    curr_voice = voiceID

@t_H2RNG_audiocallback
def audiocallback(wav, numsamples): #, isEng):
    global isSpeaking

    # log.info(F"audiocallback: {numsamples}")

    if not player:
        set_player()

    if not player:
        log.error("Hear2Read no voice found exiting synthesis")
        return CALLBACK_ABORT_SYNTHESIS
    
    try:
        if not isSpeaking:
            player.stop()
            return CALLBACK_ABORT_SYNTHESIS

        if not wav:
            isSpeaking = False
            player.idle()
            onIndexReached(None)
            return CALLBACK_ABORT_SYNTHESIS
        
        # prevByte = 0
        
        #write wav to file to test output
        # with wave.open(os.path.join(H2RNG_DATA_DIR, str(i) + ".wav"), "w") as f:
            # f.setnchannels(1)
            # f.setsampwidth(2)
            # f.setframerate(16000)
            # f.writeframes(wav_str)
        # i=i+1
            
        player.feed(wav, size=numsamples * sizeof(c_int16))

        return CALLBACK_CONTINUE_SYNTHESIS
        
    except Exception as e:
        log.error(f"audiocallback FAILED: {e}", exc_info=True)
        
@t_H2RNG_indexcallback
def indexcallback(index):
    onIndexReached(index)
    return CALLBACK_CONTINUE_SYNTHESIS

@t_H2RNG_donecallback
def donecallback():
    print(f"{LOG_TAG}: donecallback: Entered")
    onDone()
    return CALLBACK_CONTINUE_SYNTHESIS

class BgThread(threading.Thread):
    def __init__(self):
        super().__init__(
            name=f"{self.__class__.__module__}.{self.__class__.__qualname__}",
            daemon=True
            )

    def run(self):
        while True:
            func, args, kwargs = bgQueue.get()
            if not func:
                break
            try:
                func(*args, **kwargs)
            except Exception as e:
                log.error(f"Error running function from queue: {e}", 
                          exc_info=True)
            bgQueue.task_done()

def _execWhenDone(func, *args, mustBeAsync=False, **kwargs):
    if mustBeAsync or bgQueue and bgQueue.unfinished_tasks != 0:
        # Either this operation must be asynchronous or There is still an operation in progress.
        # Therefore, run this asynchronously in the background thread.
        bgQueue.put((func, args, kwargs))
    else:
        func(*args, **kwargs)
        
def _speak(text, params):
    if not H2RNG_SpeakDLL:
        return EE_INTERNAL_ERROR
    global isSpeaking
    isSpeaking = True
    
    text2=text.encode('utf8',errors='ignore')
    
    # log.info("_speak calling H2RNG_SpeakDLL.H2R_Speak_synthesizeText: " + text + " lenscale: " + str(params.phoneLen) + ", amplitude: " + str(params.volume))
    returncode = H2RNG_SpeakDLL.H2R_Speak_synthesizeSSML(text2, params)
    return returncode

def findNextTerminator(string, start):
    index = start

    whitespace = { " ", "\r", "\t", "\n"  }
    while (index < len(string)) :

        if (string[index] == "." or 
            string[index] == "!" or
            string[index] == "?" or
            string[index] == ";" or
            ord(string[index]) == 0x0964) :

            if (string[index + 1] in whitespace):
                break
        index += 1

    if start == index: 
        return 0
    
    return index
    
def characterMode(text):
    
    if len(text) > 1:
        return text
        
    unicodeHex = ord(text)
    if unicodeHex == 0x0901:
    # chandrabindu
        return "चंद्रबिंदु."
    if unicodeHex == 0x0902:
    # anuswaara
        return "अनुस्वार."
    if unicodeHex == 0x0901:
    # visarga
        return "विसर्ग."
    if 0x0915 <= unicodeHex <= 0x0939:
    # add a to consonants
        return text #+ chr(0x093E) + "."
    if unicodeHex == 0x093C:
    # nukta
        return "नुक्ता."
    # if 0x093E <= unicodeHex <= 0x094C:
    # # vowel signs - convert to independent vowel
        # return chr(unicodeHex - 0x38) + "."
    if unicodeHex == 0x094D:
    # halant
        return "हलन्त."
        
    if unicodeHex == 0x0981:
        return "চন্দ্ৰবিন্দু"
    if unicodeHex == 0x0982:
        return "উনস্বৰ"
    if unicodeHex == 0x0983:
        return "বিসৰ্গ"
    if unicodeHex == 0x09CD:
        return "হছন্ত"
        
    if unicodeHex == 0x0D4D:
        return "ചന്ദ്രക്കല"
    if unicodeHex == 0x0D02:
        return "അനുസ്വാരം"
    if unicodeHex == 0x0D03:
        return "വിസർഗം"
        
    return text
    
def asm_replacement_rules(text):
# replace bengali ra and nuqta combinations with single char
    return (text.replace("র","ৰ")
                .replace("য়","য়")
                .replace("ড়","ড়")
                .replace("ড়","ৰ")
                .replace("ঢ়","ঢ়")
                .replace("ঢ়","ৰ্হ"))

def mal_replacement_rules(text):
# replace zwj combination chillu  and nuqta combinations with single char
    return (text.replace("ര്‍","ർ")
                .replace("ല്‍","ൽ")
                .replace("ള്‍","ൾ")
                .replace("ന്‍","ൻ")
                .replace("ണ്‍","ൺ")
                .replace("ക്‍","ൿ"))

def mar_replacement_rules(text):
# remove zwj from halant zwj combos 
    return (text.replace("्‍","्")
                .replace("्‌","्"))


def speak(text: str, params: SpeechParams):
    # log.info(f"_H2R_NG_Speak speak() text = {text}, params: {params.speed}, {params.pitch}, {params.volume}, {params.charMode}")
    # convert ascii digits to devanagari
    
    # if not text.isascii():
        # apply language relevant text preprocessing
        # if getCurrentVoice() and getCurrentVoice().split("-")[0] == "as":
            # text = asm_replacement_rules(text)
            
        # replace english digits with indic
        # digit_offset = digit_offsets.get(getCurrentVoice().split("-")[0], 0)
        # text = ''.join([chr(ord(x) + digit_offset) if 48 <= ord(x) <= 58 else x for x in text])
    
    # break text info individual sentences if necessary and send only 1 sentence at a time to DLL
    # end of sentence is period or denda
#    text=text.encode('utf8',errors='ignore')

    if not player:
        set_player()

    if params.charMode:
        _execWhenDone(_speak, characterMode(text), params, mustBeAsync=True)
        return
    else:
        _execWhenDone(_speak, text.replace("।", "."), params, mustBeAsync=True)
        return

def speak_silence(time: int):
    """Speaks silence. Used for break between Indic and English text

    @param time: break time in milliseconds
    @type time: int
    """
    one_sec = qual_to_hz[curr_qual]
    # log.info(f"H2R playing silence frames: {int(one_sec * time/1000)}")
    if player:
        player.feed(bytes(int(one_sec * time/1000)))

def stop():
    global isSpeaking
    # Kill all speech from now.
    # We still want parameter changes to occur, so requeue them.
    params = []
    try:
        while True:
            item = bgQueue.get_nowait()
            if item[0] != _speak:
                params.append(item)
            bgQueue.task_done()
            
    except queue.Empty:
        # Let the exception break us out of this loop, as queue.empty() is not reliable anyway.
        pass
    for item in params:
        bgQueue.put(item)
    isSpeaking = False
#    H2RNG_SpeakDLL.H2R_Speak_stop();

    if player:
        player.stop()

    if EngSynth:
        EngSynth.cancel()

def pause(switch):
    if player:
        player.pause(switch)
    if EngSynth:
        EngSynth.pause(switch)

def set_player():
    global player, curr_qual
    curr_v = getCurrentVoice()
    if not curr_v:
        return
    curr_attrs = curr_v.split("-")
    qual = curr_attrs[-1][:3]

    if curr_qual != qual or not player:
        if player:
            player.close()
        curr_qual = qual
        
        # Compatibility for NVDA version < 2025.1
        try:
            audioDevice = config.conf["audio"]["outputDevice"]
        except:
            audioDevice = config.conf["speech"]["outputDevice"]

        player = nvwave.WavePlayer(channels=1,
                            samplesPerSec=qual_to_hz[qual],
                            bitsPerSample=16,
                            outputDevice=audioDevice)#,
                            # buffered=True) deprecated, removed 2025.1


def _setVoiceByIdentifier(voiceID):  
    # log.info(f"_setVoiceByIdentifier: {voiceID}")  
    if voiceID:
        voice_attrs = voiceID.split("-")
    else:
        return EE_NOT_FOUND
        
    if voiceID == getCurrentVoice():
        return EE_OK
    
    if not H2RNG_SpeakDLL:
        return EE_NOT_FOUND
     
    # workaround to set dipal's voice as default for guj, if the json doesn't 
    # contain the correct ID
    # TODO check this
    # if (voice_attrs[0] == "gu" and voice_attrs[1] == "h2r"
    #     and H2RNG_SpeakDLL.H2R_Speak_GetSpeakerID() <= 0):
    #     setCurrentVoice(voiceID)
    #     H2RNG_SpeakDLL.H2R_Speak_SetVoice(
    #         c_char_p(encodeH2RSpeakString(voiceID)),
    #         c_char_p(encodeH2RSpeakString(str(H2RNG_VOICES_DIR))))
    #     return(H2RNG_SpeakDLL.H2R_Speak_SetSpeakerID(DIPAL_ID))
        
    # workaround to set amarpreet's voice as default for pan 
    # if (voice_attrs[0] == "pa" and voice_attrs[1] == "tdilh2r"
    #     and H2RNG_SpeakDLL.H2R_Speak_GetSpeakerID() <= 0):
    #     setCurrentVoice(voiceID)
    #     H2RNG_SpeakDLL.H2R_Speak_SetVoice(
    #         c_char_p(encodeH2RSpeakString(voiceID)),
    #         c_char_p(encodeH2RSpeakString(str(H2RNG_VOICES_DIR))))
    #     return(H2RNG_SpeakDLL.H2R_Speak_SetSpeakerID(AMARPREET_ID))
        
    setCurrentVoice(voiceID)
    #TODO async - handle exceptions differently
    return(H2RNG_SpeakDLL.H2R_Speak_SetVoice(
        c_char_p(encodeH2RSpeakString(voiceID)),
        c_char_p(encodeH2RSpeakString(str(H2RNG_VOICES_DIR)))))
    
# TODO check if non blocking neccessary
# def setVoiceByIdentifier(voiceID=None):
#     _execWhenDone(_setVoiceByIdentifier, voiceID=voiceID, mustBeAsync=True)

#TODO default voice
def setVoiceByLanguage(lang):
    # log.info(f"_H2R_NG_Speak:setVoiceByLanguage: {lang}")
    
    lang = lang.split("_")[0]
    
    if lang == "en":
        setCurrentVoice(en_voice)
        return en_voice
        
    #Get all files in the Voices Directory
    pathName = H2RNG_VOICES_DIR
    # log.info(f"_H2R_NG_Speak:setVoiceByLanguage - looking in {H2RNG_VOICES_DIR}")

    file_list = os.listdir(pathName)
    
    for file_name in file_list:
        parts = file_name.split(".")
        if parts[-1] == "onnx":
        # Found one of the NVDA Addon onnx voice file
            # log.info("_H2R_NG_Speak setVoiceByLanguage: parts = %s", parts[0])
            file_lang = parts[0].split("-")[0]
            if file_lang == lang and (f"{file_name}.json") in file_list:
                # matching language
                
                # log.info(f"_H2R_NG_Speak:setVoiceByLanguage - found {file_lang} for lang {lang}")

                hr = _setVoiceByIdentifier(parts[0])
                setCurrentVoice(parts[0])
                set_player()
                return getCurrentVoice()
                
                # TODO: send error message on fail

    for file_name in file_list:
        parts = file_name.split(".")
        if parts[-1] == "onnx":
            if (f"{file_name}.json") in file_list:
                hr = _setVoiceByIdentifier(parts[0])
                setCurrentVoice(parts[0])
                set_player()
                return getCurrentVoice()
    
    log.warn("Hear2Read no voices found")
    return None

    # TODO inform user
    # exceptionString = "No Voices found  '" + lang + "'"
    # raise Exception(exceptionString)
    # return None


def init_eng_synth(default_synth="oneCore"):

    eng_synth = _h2r_config.get(SCT_EngSynth, {}).get(ID_EnglishSynthName, default_synth)
    eng_voice = _h2r_config.get(SCT_EngSynth, {}).get(ID_EnglishSynthVoice, "")
    eng_variant = _h2r_config.get(SCT_EngSynth, {}).get(ID_EnglishSynthVariant, "")

    # log.info(f"init_eng_synth: got synth and voice from config: {eng_synth}, {eng_voice}")

    set_eng_synth(eng_synth=eng_synth)

    if EngSynth:
        supportedSettings = EngSynth.supportedSettings
        # log.info(f"got supported eng settings: {supportedSettings}")

        if eng_voice and eng_voice in get_eng_synth_voicelist().keys():
            set_eng_synth_voice(eng_voice)
        if eng_variant and eng_variant in get_eng_synth_variantlist().keys():
            set_eng_synth_variant(eng_variant)

        if "pitch" in supportedSettings:
            set_eng_synth_pitch(_h2r_config[SCT_EngSynth][ID_EnglishSynthPitch])
        if "rate" in supportedSettings:
            set_eng_synth_rate(_h2r_config[SCT_EngSynth][ID_EnglishSynthRate])
        if "volume" in supportedSettings:
            set_eng_synth_volume(_h2r_config[SCT_EngSynth][ID_EnglishSynthVolume])
        if "inflection" in supportedSettings:
            set_eng_synth_inflection(_h2r_config[SCT_EngSynth][ID_EnglishSynthInflection])


def set_eng_synth(eng_synth):
    global EngSynth, EngVoices
    
    try:
        if EngSynth:
            # TODO: don't change if same synth
            # if EngSynth.name == eng_synth:
            #     log.info("")
            EngSynth.cancel()
            EngSynth.terminate()
            del EngSynth
    except NameError as e:
        # log.info("EngSynth not defined. Ignoring")
        pass

    # eng_synth = config.conf.get("hear2read", {}).get("engSynth", eng_synth)
    # eng_voice = config.conf.get("hear2read", {}).get("engVoice", "")

    EngSynth = getSynthInstance(eng_synth)
    EngVoices = EngSynth._get_availableVoices()
    eng_voice = ""
    
    # if eng_voice not in EngVoices.keys():
    for voice in EngVoices.values():
        # log.info(f"onecore voice: {voice.displayName}, id: {voice.id}")
        # Hardcoding the voice as well for now
        if ((voice.language and voice.language.startswith("en")) 
                or (not voice.language 
                    and "english" in voice.displayName.lower())):
            eng_voice = voice.id
            break

    if eng_voice:
        EngSynth._set_voice(eng_voice)
    # _h2r_config[SCT_EngSynth][ID_EnglishSynthName] = EngSynth.name
    # _h2r_config[SCT_EngSynth][ID_EnglishSynthVoice] = EngSynth.voice
    return True

def get_eng_synth_voice():
    if EngSynth:
        return EngSynth.voice

def set_eng_synth_voice(voice_id):
    if voice_id not in get_eng_synth_voicelist().keys():
        log.warn(f"English voice {voice_id} not found in synthesizer, skipping")
        return
    # log.info(f"set_eng_voice: {voice_id}")
    if EngSynth:
        EngSynth._set_voice(voice_id)

    # log.info(f"voice changed to: {get_eng_synth_voice()}")
    if get_eng_synth_voice() != voice_id:
        # log.info("failed changing the voice. trying change_voice")
        changeVoice(EngSynth, voice_id)
        # log.info(f"2nd attempt voice changed to: {get_eng_synth_voice()}")

    # _h2r_config[SCT_EngSynth][ID_EnglishSynthVoice] = EngSynth.voice

def get_eng_synth_variant():
    if EngSynth:
        try:
            return EngSynth._get_variant()
        except NotImplementedError as e:
            pass
    return ""

def set_eng_synth_variant(variant):
    if variant not in get_eng_synth_variantlist():
        log.warn(f"English variant {variant} not found in synthesizer, skipping")
        return
    if EngSynth:
        EngSynth._set_variant(variant)

def get_eng_synth_rate():
    if EngSynth:
        return EngSynth._get_rate()

def set_eng_synth_rate(rate):
    if EngSynth:
        EngSynth._set_rate(rate)

def get_eng_synth_pitch():
    # log.info(f"Got english synth pitch: {EngSynth._get_pitch()}")
    if EngSynth:
        return EngSynth._get_pitch()

def set_eng_synth_pitch(pitch):
    # log.info(f"Setting english synth pitch to: {pitch}")
    if EngSynth:
        EngSynth._set_pitch(pitch)

def get_eng_synth_volume():
    if EngSynth:
        return EngSynth._get_volume()

def set_eng_synth_volume(volume):
    if EngSynth:
        EngSynth._set_volume(volume)

def get_eng_synth_inflection():
    if EngSynth:
        return EngSynth._get_inflection()

def set_eng_synth_inflection(inflection):
    if EngSynth:
        EngSynth._set_inflection(inflection)

def get_eng_synth_name():
    if EngSynth:
        return EngSynth.name
    else:
        return ""

def get_eng_synth_desc():
    if EngSynth:
        return EngSynth.description
    else:
        return ""
    
def get_eng_synth():
    # log.info("Hear2Read")
    try:
        return EngSynth if EngSynth else None
    except NameError as e:
        return None
    
def get_eng_synth_voicelist() -> OrderedDict:
    if EngSynth:
        try:
            all_voices = EngSynth._get_availableVoices()
            # log.info(f"got all voices: {all_voices}")
            return  OrderedDict((id, voice_info)
                for id, voice_info in all_voices.items()
                if ((voice_info.language and voice_info.language.startswith("en")) 
                    or (not voice_info.language 
                        and "english" in voice_info.displayName.lower()))
            )
        except Exception as e:
            log.warn(f"get_eng_synth_voicelist: Unable to list voices from \"{EngSynth.name}\"")

    return OrderedDict()


def get_eng_synth_variantlist():
    if EngSynth:
        try:
            return EngSynth._get_availableVariants()
        except NotImplementedError as e:
            log.warn(f"get_eng_synth_variantlist: Unable to list variants from \"{EngSynth.name}\"")
    return {}
    
def speak_eng(speech_sequence):
    # TODO throw exception if not?
    if EngSynth:
        # log.info(f"Speaking English: {speech_sequence}")
        EngSynth.speak(speech_sequence)

def H2R_Speak_errcheck(res, func, args):
    if res != EE_OK:
        raise RuntimeError("%s: code %d" % (func.__name__, res))
    return res

def initialize(idxCallback = defaultIndexCallback, doneCallback = defaultDoneCallback):
    """
    @param idxCallback: A function which is called when eSpeak reaches an index.
        It is called with one argument:
        the number of the index or C{None} when speech stops.
    """
    global H2RNG_SpeakDLL, bgThread, bgQueue, onIndexReached, onDone

    H2RNG_SpeakDLL = cdll.LoadLibrary(str(H2RNG_ENGINE_DLL_PATH))

    H2RNG_SpeakDLL.H2R_Speak_init.argtypes=[c_char_p,Callbacks]
    H2RNG_SpeakDLL.H2R_Speak_init.errcheck=H2R_Speak_errcheck
    H2RNG_SpeakDLL.H2R_Speak_synthesizeText.errcheck=H2R_Speak_errcheck
    H2RNG_SpeakDLL.H2R_Speak_synthesizeText.argtypes=(c_char_p, SpeechParams)
    H2RNG_SpeakDLL.H2R_Speak_synthesizeSSML.errcheck=H2R_Speak_errcheck
    H2RNG_SpeakDLL.H2R_Speak_synthesizeSSML.argtypes=(c_char_p, SpeechParams)
    H2RNG_SpeakDLL.H2R_Speak_SetVoice.argtypes=[c_char_p,c_char_p]
    H2RNG_SpeakDLL.H2R_Speak_SetVoice.errcheck=H2R_Speak_errcheck
            
    callbacks = Callbacks(audiocallback, indexcallback, donecallback) # doneCallback in 2.0
    
    H2RNG_SpeakDLL.H2R_Speak_init(c_char_p(encodeH2RSpeakString(str(H2RNG_DATA_DIR))), callbacks)
    
    # player = nvwave.WavePlayer(channels=1, samplesPerSec=qual_to_hz[en_qual], bitsPerSample=16, outputDevice=config.conf["speech"]["outputDevice"], buffered=False)

    onIndexReached = idxCallback
    onDone = doneCallback
    bgQueue = queue.Queue()
    bgThread = BgThread()
    bgThread.start()

    init_eng_synth()


def terminate():
    global bgThread, bgQueue, player, H2RNG_SpeakDLL, onIndexReached, onDone, EngSynth
    stop()
    if bgQueue:
        bgQueue.put((None, None, None))
    if bgThread:
        bgThread.join()
    if H2RNG_SpeakDLL:
        H2RNG_SpeakDLL.H2R_Speak_Terminate()
        del H2RNG_SpeakDLL
    bgThread=None
    bgQueue=None
    if player:
        player.close()
    player=None
    onIndexReached = defaultIndexCallback
    onDone = defaultDoneCallback
    if EngSynth:
        EngSynth.cancel()
        EngSynth.terminate()
        del EngSynth

def info():
    # Python 3.8: a path string must be specified, a NULL is fine when what we need is version string.
    if H2RNG_SpeakDLL:
        return H2RNG_SpeakDLL.H2R_Speak_Info(None)