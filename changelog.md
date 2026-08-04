# Changelog

## Unreleased

- Added NVDA's Rate boost setting (Voice Settings dialog and synth
settings ring): with rate boost enabled, the rate range's top end is
extended from 2.0x to 4.0x speech speed, so the same rate position
speaks faster, like NVDA's own drivers (e.g. Windows OneCore).

## 0.3.1

- Fixed the join between two consecutive voice outputs (e.g. between
say-all lines) breaking the reading rhythm: the next utterance's first
chunk is now pre-synthesized while the current one is still playing,
so the two utterances join with only the natural punctuation pause
instead of an extra ~0.2-0.7s of dead air from model inference.
- The pause after the final sentence of each utterance now follows the
same punctuation-based rhythm as the pauses between sentences, so a
question gets a longer pause and a comma a shorter one at line breaks.
- When NVDA reads long text line by line (say-all) or paragraph by
paragraph, a line that breaks mid-sentence no longer sounds like the end
of a sentence: the pause after each utterance follows the punctuation
that line actually ends with, instead of the closing period the engine
appends internally for prosody.

## 0.3.0

- Fixed sentences overlapping while reading long text: the driver now
speaks all queued utterances one at a time from a single worker thread
instead of starting a new synthesis thread per speak() call, which fed
concurrent audio into the same WavePlayer during say-all.
- Fixed the end of each sentence being cut off abruptly while reading:
the driver now reports NVDA's IndexCommands via synthIndexReached at the
exact audio position, so NVDA's speech manager paces say-all line-by-line
instead of interrupting the current sentence.
- Slightly longer, more natural pauses between sentences when reading
long text.

## 0.2.0

- Added a settings panel (NVDA Settings -> Inflect Micro TTS) to
download additional voices of the Inflect TTS family from Hugging Face
(Inflect Nano v2 at the moment), remove them, and apply a voice to the
synthesizer without restarting NVDA.
- The synthesizer now exposes a Voice setting; downloaded voices appear
in NVDA's voice list automatically.

## 0.1.0

Initial release of InflectMicroTTS for NVDA 2026.1 (64-bit).

- Provides a single English male voice driven by the Inflect Micro v2
  ONNX model (24 kHz), synthesized locally with ONNX Runtime.
- Fully offline: the model, onnxruntime, numpy, phonemizer, espeak-ng and
  all other dependencies are bundled inside the add-on.
- Synth settings: rate, volume, variation and seed.
- Sentence-by-sentence audio streaming for faster response.
