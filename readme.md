# Inflect Micro TTS

A high-quality local neural text-to-speech voice for NVDA, based on the
[Inflect Micro v2](https://github.com/owenawsong/Inflect) model by
[Owen Song](https://github.com/owenawsong).

The add-on bundles the model, the ONNX Runtime and every other dependency,
so the voice works **fully offline**: after installation, no internet
access is ever needed.

- **Voices:** the bundled Inflect Micro v2 voice (24 kHz) plus
  additional Inflect TTS voices that can be downloaded from
  NVDA Settings -> Inflect Micro TTS (e.g. the smaller and faster
  Inflect Nano v2).
- **Settings:** voice, rate, rate boost, volume, variation and seed,
  available from NVDA's synth settings ring and the Voice Settings
  dialog.
- **Platform:** NVDA 2026.1, 64-bit (CPython 3.13).

## Installing

1. Build the add-on (see below) or download a pre-built
   `InflectMicroTTS-0.1.0.nvda-addon`.
2. Open the file with NVDA (or use NVDA menu -> Tools -> Add-on manager ->
   Install), confirm the installation and restart NVDA.
3. In NVDA Settings -> Speech, choose **Inflect Micro v2** from the
   synthesizer list. The first utterance takes a few seconds while the
   model is loaded; afterwards it speaks normally.

## Downloading more voices

Open **NVDA Settings -> Inflect Micro TTS**. The panel lists the bundled
voice and any other voices of the same Inflect TTS family:

- Select a voice and press **Download** to fetch its model files from
  Hugging Face (a progress bar shows the transfer). The voice becomes
  available immediately.
- Press **Remove** to delete a downloaded voice again (the bundled voice
  cannot be removed).
- Press **Apply** to switch the synthesizer to a voice right away, or
  pick it later from the **Voice** setting in NVDA's Speech settings.

Downloaded voices are stored per-user under
`%APPDATA%\inflectMicroTTS\models\<voiceId>\`. The first utterance of a
newly downloaded voice pays the model-loading cost once; afterwards it
stays cached for the session.

## Building from source

Requirements: Python 3.13 64-bit, [uv](https://docs.astral.sh/uv/),
internet access (only needed while building, not while using the add-on).

```bat
uv sync
python tools/vendor_deps.py
python tools/fetch_model.py
uv run scons
```

This produces `InflectMicroTTS-0.1.0.nvda-addon` in the repository root.

- `tools/vendor_deps.py` downloads the Windows x64 wheels for Python 3.13
  (onnxruntime, numpy, phonemizer, num2words, Unidecode and
  espeakng-loader, plus their dependencies) and unpacks them into
  `addon/synthDrivers/_inflectMicro/lib/`. It only needs the standard
  library, so run it with any Python that has pip (e.g. the system
  interpreter).
- `tools/fetch_model.py` downloads the Inflect Micro v2 ONNX runtime files
  (duration and decode graphs, config and text frontend) from Hugging Face
  into `addon/synthDrivers/_inflectMicro/model/`.

### Self-test (no NVDA needed)

After running the two tools above you can verify the whole pipeline:

```bat
python tools/test_synth.py
```

This synthesizes a phrase and writes `inflect-test.wav`.

## How it works

`addon/synthDrivers/inflectMicroTTS.py` is a standard NVDA `SynthDriver`.
Speech is synthesized on a background thread with the bundled
`_inflectMicro` engine:

1. The text is normalized and converted to phonemes with the model's own
   frontend (num2words + Unidecode + phonemizer using the bundled
   espeak-ng library).
2. Two small ONNX graphs (duration predictor and waveform decoder) are run
   with ONNX Runtime on the CPU, producing 24 kHz mono PCM.
3. The audio is streamed through `nvwave.WavePlayer` as it is generated,
   so playback starts before long messages finish synthesizing.

The model is loaded lazily the first time speech is requested and stays
cached for the session; the `terminate()` hook releases it when the
synthesizer is switched or NVDA exits.

## Limitations

- All voices are English (24 kHz); there is no pitch or language
  selection.
- Downloading a voice requires an internet connection (only needed while
  downloading, not while speaking).
- Text-to-audio conversion is CPU-based. The model synthesizes several
  times faster than real time on modern hardware, but a short delay is
  noticeable at the start of the first utterance of a session.
- Character spelling, phonetic spelling and index commands are not
  supported by the model; NVDA falls back to its built-in handling.
- `pause` (speech on demand) is not supported yet.

## License and credits

- Add-on code: GNU General Public License, version 2 or later
  (see `COPYING.txt`).
- Inflect Micro v2 model, weights and frontend code: Apache License 2.0,
  copyright Owen Song.
- Third-party components bundled with the add-on retain their own
  licenses; see `THIRD_PARTY_NOTICES.md` for details.

Note: the add-on embeds the GPL-3.0-licensed espeak-ng library (via
`espeakng-loader`) and the phonemizer package as separate components, as
the upstream Inflect project does. If you plan to redistribute the add-on,
review the notices in `THIRD_PARTY_NOTICES.md`.
