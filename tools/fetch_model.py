# pyright: reportUnusedCallResult=false
"""Fetch the Inflect Micro v2 ONNX model files into the add-on.

Downloads the minimal runtime subset of the official Hugging Face model
repository (``owensong/Inflect-Micro-v2-ONNX``) into
``addon/synthDrivers/_inflectMicro/model/`` so that the add-on is fully
offline. The model and its weights are Apache-2.0 licensed by Owen Song.

Usage (from the repository root):

    python tools/fetch_model.py
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "addon" / "synthDrivers" / "_inflectMicro" / "model"

#: Base URL of the official ONNX model repository.
BASE_URL = "https://huggingface.co/owensong/Inflect-Micro-v2-ONNX/resolve/main"

#: Runtime files needed for ONNX inference (must match provision.MODEL_FILES).
FILES: tuple[str, ...] = (
	"onnx/duration.onnx",
	"onnx/decode.onnx",
	"config.json",
	"inflect_vits_frontend.py",
	"inflect_nano_v2_frontend.py",
	"runtime/text/__init__.py",
	"runtime/text/cleaners.py",
	"runtime/text/symbols.py",
)


def _human(size: int) -> str:
	if size >= 1024 * 1024:
		return f"{size / 1024 / 1024:.1f} MB"
	return f"{size / 1024:.0f} KB"


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--base-url",
		default=BASE_URL,
		help="Override the Hugging Face base URL (e.g. a mirror).",
	)
	args = parser.parse_args()

	totalBytes = 0
	for rel in FILES:
		dest = MODEL_DIR / rel
		dest.parent.mkdir(parents=True, exist_ok=True)
		url = f"{args.base_url}/{rel}"
		print(f"Downloading {rel} ...", end=" ", flush=True)
		request = urllib.request.Request(
			url, headers={"User-Agent": "InflectMicroTTS-addon/0.1"}
		)
		with urllib.request.urlopen(request) as response, dest.open("wb") as out:
			shutil.copyfileobj(response, out)
		size = dest.stat().st_size
		totalBytes += size
		print(_human(size))

	print(f"Fetched {len(FILES)} files ({_human(totalBytes)}) into {MODEL_DIR}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
