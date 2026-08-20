# Build customizations
# Change this file instead of sconstruct or manifest files, whenever possible.

from site_scons.site_tools.NVDATool.typings import (
	AddonInfo,
	BrailleTables,
	SymbolDictionaries,
	SpeechDictionaries,
)

# Since some strings in `addon_info` are translatable,
# we need to include them in the .po files.
# Gettext recognizes only strings given as parameters to the `_` function.
# To avoid initializing translations in this module we simply import a "fake" `_` function
# which returns whatever is given to it as an argument.
from site_scons.site_tools.NVDATool.utils import _

# Add-on information variables
addon_info = AddonInfo(
	# add-on Name/identifier, internal for NVDA
	addon_name="NVDAInflectMicroTTS",
	# Add-on summary/title, usually the user visible name of the add-on
	# Translators: Summary/title for this add-on
	# to be shown on installation and add-on information found in add-on store
	addon_summary=_("NVDAInflect Micro TTS"),
	# Add-on description
	# Translators: Long description to be shown for this add-on on add-on information from add-on store
	addon_description=_(
		"""A high-quality local neural text-to-speech voice for NVDA based on the Inflect Micro v2 model created by Owen Song.
The model, the ONNX Runtime and all other dependencies are bundled with the add-on,
so the voice works fully offline.
Speaks English with a fixed male voice at 24 kHz and offers rate, rate
boost, volume, variation and seed settings, plus optional GPU
acceleration via DirectML (Auto/CPU/GPU compute device choice).
i use ai agent in this addon development"""
	),
	# version
	addon_version="2026.1.3",
	# Brief changelog for this version
	# Translators: what's new content for the add-on version to be shown in the add-on store
	addon_changelog=_(
		"""Fixed a bug where reloading NVDA add-ons (NVDA+Ctrl+F3) caused
multiple "Inflect Micro TTS" entries to appear in NVDA Settings.
The settings panel is now properly cleaned up on plugin unload."""
	),
	# Author(s)
	# Translators: not used (metadata only).
	addon_author="Anuj Sharma <anujj87@hotmail.com>",
	# URL for the add-on documentation support
	addon_url="https://github.com/anujj87/NVDAInflectMicroTTS",
	# URL for the add-on repository where the source code can be found
	addon_sourceURL="https://github.com/anujj87/NVDAInflectMicroTTS",
	# Documentation file name
	addon_docFileName="readme.html",
	# Minimum NVDA version supported
	addon_minimumNVDAVersion="2026.1.0",
	# Last NVDA version supported/tested
	addon_lastTestedNVDAVersion="2026.1.0",
	# Add-on update channel (default is None, denoting stable releases,
	# and for development releases, use "dev".)
	# Do not change unless you know what you are doing!
	addon_updateChannel="dev",
	# Add-on license such as GPL 2
	addon_license="GPL 2",
	# URL for the license document the add-on is licensed under
	addon_licenseURL="https://www.gnu.org/licenses/old-licenses/gpl-2.0.html",
)

# Define the python files that are the sources of your add-on.
# The underscore-prefixed package holds the bundled engine, frontend and
# vendored dependencies; it is never loaded as a synthesizer by NVDA itself.
pythonSources: list[str] = [
	"addon/synthDrivers/*.py",
	"addon/synthDrivers/_inflectMicro/*.py",
	"addon/globalPlugins/inflectMicroTTS/*.py",
]

# Files that contain strings for translation. Usually your python sources
i18nSources: list[str] = pythonSources + ["buildVars.py"]

# Files that will be ignored when building the nvda-addon file
# Paths are relative to the addon directory, not to the root directory of your addon sources.
# You can either list every file (using "/") as a path separator,
# or use glob expressions.
excludedFiles: list[str] = []

# Base language for the NVDA add-on
baseLanguage: str = "en"

# Markdown extensions for add-on documentation
markdownExtensions: list[str] = []

# Custom braille translation tables
brailleTables: BrailleTables = {}

# Custom speech symbol dictionaries
symbolDictionaries: SymbolDictionaries = {}

# Custom speech dictionaries (distinct from symbol dictionaries above)
speechDictionaries: SpeechDictionaries = {}
