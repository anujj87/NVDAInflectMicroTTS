# pyright: reportUnusedCallResult=false
"""Vendor the InflectMicroTTS add-on's Python dependencies into ``lib/``.

Downloads Windows x64 wheels built for CPython 3.13 (the interpreter
embedded in NVDA 2026.1) and unpacks them into
``addon/synthDrivers/_inflectMicro/lib/`` so that the add-on is fully
offline. Nothing needs to be installed on the user's machine.

Usage (from the repository root):

    python tools/vendor_deps.py

Any Python 3.13+ with pip works; run it directly with the system
interpreter (``uv run`` is not required and uv-managed venvs usually ship
no pip). To override the target interpreter (only if you build against a
different NVDA version), pass ``--python-version`` and ``--abi``
explicitly.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "addon" / "synthDrivers" / "_inflectMicro" / "lib"

#: Runtime packages required by the Inflect Micro v2 ONNX engine and its
#: text frontend. pip resolves and downloads all transitive dependencies.
PACKAGES: tuple[str, ...] = (
	"onnxruntime",
	"numpy",
	"phonemizer",
	"num2words",
	"Unidecode",
	"espeakng-loader",
)


def _downloadCommand() -> list[str]:
	"""Return the wheel-download command.

	This script only needs the standard library, so run it with any Python
	that has pip installed (e.g. the system interpreter). A uv-managed
	venv usually ships no pip, so a clear error is raised in that case.
	"""
	try:
		subprocess.run(
			[sys.executable, "-m", "pip", "--version"],
			check=True,
			capture_output=True,
		)
	except Exception:
		raise SystemExit(
			"pip is not available in the current interpreter. Run this script "
			+ "with a Python that includes pip, e.g. python tools/vendor_deps.py "
			+ "(not via 'uv run')."
		) from None
	return [sys.executable, "-m", "pip", "download"]


def main() -> int:
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument(
		"--python-version",
		default="3.13",
		help="Target CPython version (NVDA 2026.1 embeds 3.13).",
	)
	parser.add_argument("--abi", default="cp313", help="Target wheel ABI tag.")
	args = parser.parse_args()

	LIB_DIR.mkdir(parents=True, exist_ok=True)

	with tempfile.TemporaryDirectory() as tmp:
		tmpDir = pathlib.Path(tmp)
		command = [
			*_downloadCommand(),
			"--only-binary=:all:",
			"--platform", "win_amd64",
			"--python-version", args.python_version,
			"--implementation", "cp",
			"--abi", args.abi,
			"--dest", str(tmpDir),
			*PACKAGES,
		]
		print("Running:", " ".join(command))
		subprocess.check_call(command)

		wheels = sorted(tmpDir.glob("*.whl"))
		if not wheels:
			print("No wheels were downloaded. Aborting.", file=sys.stderr)
			return 1

		# Clear the previous vendor directory (keeping the README) so that
		# stale or upgraded packages do not linger.
		for entry in LIB_DIR.iterdir():
			if entry.name == "README.md":
				continue
			if entry.is_dir():
				shutil.rmtree(entry, ignore_errors=True)
			else:
				entry.unlink()

		totalBytes = 0
		for wheel in wheels:
			size = wheel.stat().st_size
			totalBytes += size
			print(f"Extracting {wheel.name} ({size / 1024 / 1024:.1f} MB)")
			with zipfile.ZipFile(wheel) as archive:
				archive.extractall(LIB_DIR)

		print(f"Vendored {len(wheels)} wheels ({totalBytes / 1024 / 1024:.1f} MB) into {LIB_DIR}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
