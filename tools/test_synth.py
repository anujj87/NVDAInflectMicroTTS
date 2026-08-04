# pyright: reportUnusedCallResult=false
"""Synthesize a test phrase to a WAV file using the bundled engine.

Runs the full pipeline (frontend -> phonemizer/espeak-ng -> ONNX Runtime)
without NVDA, which makes it easy to verify that the vendored
dependencies and the fetched model are complete and working.

Usage (from the repository root):

    python tools/test_synth.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

# Make the add-on's synthDrivers package importable so that the engine
# can be imported as ``_inflectMicro.engine`` (its relative imports
# require it to be loaded as part of the package).
ADDON_SYNTH_DRIVERS = (
	pathlib.Path(__file__).resolve().parents[1] / "addon" / "synthDrivers"
)
sys.path.insert(0, str(ADDON_SYNTH_DRIVERS))


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--text", default="Hello! This is Inflect Micro v2 speaking to you.")
	parser.add_argument("--output", default="inflect-test.wav")
	parser.add_argument("--speed", type=float, default=1.0)
	parser.add_argument("--variation", type=float, default=0.667)
	parser.add_argument("--seed", type=int, default=7)
	args = parser.parse_args()

	start = time.monotonic()
	from _inflectMicro.engine import getEngine

	engine = getEngine()
	sampleRate, wav = engine.synthesize(
		args.text,
		speed=args.speed,
		variation=args.variation,
		seed=args.seed,
	)
	output = pathlib.Path(args.output)
	output.write_bytes(wav)
	seconds = time.monotonic() - start
	size = len(wav) / 1024 / 1024
	print(
		"OK: wrote {} ({:.1f} MB, {} Hz, {:.1f}s of audio) in {:.1f}s (including model load)".format(
			output,
			size,
			sampleRate,
			wav_size_to_seconds(len(wav), sampleRate),
			seconds,
		)
	)
	return 0


def wav_size_to_seconds(wavBytes: int, sampleRate: int) -> float:
	# 44-byte header, 16-bit mono.
	return (wavBytes - 44) / 2 / sampleRate


if __name__ == "__main__":
	raise SystemExit(main())
