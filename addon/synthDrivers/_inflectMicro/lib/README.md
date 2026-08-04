# Vendored dependencies

This folder is populated at build time by `tools/vendor_deps.py` and
contains the add-on's third-party Python packages, unpacked from Windows
x64 wheels built for CPython 3.13 (the interpreter embedded in NVDA
2026.1):

- onnxruntime (and its dependencies: protobuf, flatbuffers, coloredlogs,
  sympy, packaging, psutil, ...)
- numpy
- phonemizer
- num2words
- Unidecode
- espeakng-loader (includes the espeak-ng shared library for Windows)

Do not edit these files manually; re-run `uv run tools/vendor_deps.py` to
regenerate them. See `THIRD_PARTY_NOTICES.md` at the repository root for
license information.
