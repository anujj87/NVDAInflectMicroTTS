import os
from pathlib import Path

APPDATA = Path(os.getenv('APPDATA') or Path.home())
H2RNG_DATA_DIR = APPDATA / "Hear2Read-NG"
H2RNG_PHONEME_DIR = H2RNG_DATA_DIR / "espeak-ng-data"
H2RNG_DLL_NAME = "h2r-ng.dll"
H2RNG_ENGINE_DLL_PATH = H2RNG_DATA_DIR / H2RNG_DLL_NAME
# temporary path to update DLL without needing current Hear2ReadNG engine to stop (release DLL)
H2RNG_ENGINE_UPDATE_PATH = H2RNG_DATA_DIR / f"{H2RNG_DLL_NAME}.update"
H2RNG_VOICES_DIR = H2RNG_DATA_DIR / "Voices"
H2RNG_WAVS_DIR = H2RNG_DATA_DIR / "wavs"
EN_VOICE_ALOK = "en_US-arctic-medium"
ADDON_NAME = "Hear2ReadNG"