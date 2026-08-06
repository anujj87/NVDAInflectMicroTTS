# addon/synthDrivers/_inflectMicro/_probe_model.py
# Part of the InflectMicroTTS NVDA add-on.
# Copyright (C) 2026 InflectMicroTTS contributors
# This file is covered by the GNU General Public License, version 2 or later.
"""Tiny embedded ONNX model used to probe execution providers.

DirectML advertises itself even on machines without a usable Direct3D 12
device and only fails when an actual InferenceSession is created, so the
only honest availability check is to build a real session. Bundling a
model just for that would be wasteful, hence this trivial ``Relu`` graph
(1x4 float input -> 1x4 output), serialized once with the ``onnx``
package and embedded here as base64. Probing with it takes tens of
milliseconds.
"""

#: Serialized minimal ONNX graph (Relu, input "x" 1x4 f32, output "y").
PROBE_ONNX_B64 = """\
CAg6PwoMCgF4EgF5IgRSZWx1EgVwcm9iZVoTCgF4Eg4KDAgBEggKAggBCgIIBGITCgF5Eg4KDAgBEggKAggBCgIIBEIECgAQDQ==
"""
