"""Jarvis: assistente personale vocale."""

__version__ = "0.1.0"

from jarvis import vcrt as _vcrt

# prima di qualunque import di onnxruntime e soxr: sui PC senza il runtime di Visual C++ si usa quello di tools/vcrt
_vcrt.use()
