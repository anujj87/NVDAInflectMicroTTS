# Third-party notices

This add-on bundles the following third-party components. Each component
retains its own copyright and license; the notices below summarize where
the components come from and how they are licensed.

## Inflect Micro v2 (model, weights, frontend code)

- Source: https://github.com/owenawsong/Inflect
- Models: https://huggingface.co/owensong/Inflect-Micro-v2 and
  https://huggingface.co/owensong/Inflect-Micro-v2-ONNX
- License: Apache License 2.0
- Copyright: Owen Song

The add-on's engine (in `addon/synthDrivers/_inflectMicro/`) includes
code adapted from the model's official inference scripts and from
`robertbak/ha-inflect-tts` (Apache-2.0), which are distributed under the
Apache License 2.0. A copy of the Apache License is available at
https://www.apache.org/licenses/LICENSE-2.0

## onnxruntime (ONNX Runtime)

- Source: https://github.com/microsoft/onnxruntime
- License: MIT License

## numpy

- Source: https://numpy.org
- License: BSD 3-Clause

## phonemizer

- Source: https://github.com/bootphon/phonemizer
- License: GNU General Public License v3 (GPL-3)
- Note: bundled as a separate, unmodified component, as the upstream
  Inflect project does. The add-on is licensed "GPL 2 or later", which is
  compatible with distributing GPL-3 components.

## espeak-ng (via espeakng-loader)

- Source: https://github.com/espeak-ng/espeak-ng
- Loader package: https://github.com/thewh1teagle/espeakng-loader
- License: GNU General Public License v3 (GPL-3)

## num2words

- Source: https://github.com/savoirfairelinux/num2words
- License: GNU Lesser General Public License v3 (LGPL-3)

## Unidecode

- Source: https://github.com/avian2/unidecode
- License: GNU General Public License v2 or later (GPL-2+)

## Indirect dependencies (installed alongside the above)

- docopt (MIT), packaging (BSD/Apache-2.0), protobuf (BSD-3-Clause),
  flatbuffers (Apache-2.0), coloredlogs & verboselogs (MIT), sympy
  (BSD-3-Clause), psutil (BSD-3-Clause).

Full license texts are included inside the vendored packages under
`addon/synthDrivers/_inflectMicro/lib/` where available, and the model
repository ships `LICENSE` and `THIRD_PARTY_NOTICES.md` files with the
individual component notices.
