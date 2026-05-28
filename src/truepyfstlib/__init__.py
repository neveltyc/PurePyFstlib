"""
TruePyFstlib - Pure Python FST waveform file reader and writer.

FST (Fast Signal Trace) is the compact binary waveform format used by
GTKWave and Icarus Verilog. This library provides a pure-Python,
platform-independent implementation with no C dependencies.
"""

from .reader import FstReader
from .writer import FstWriter
from .common import (
    FstBlockType,
    FstScopeType,
    FstVarType,
    FstVarDir,
    FstFileType,
    FstWriterPackType,
    FstAttrType,
    FstMiscType,
    FstArrayType,
    FstEnumValueType,
    FstPackType,
    FstSupplementalVarType,
    FstSupplementalDataType,
    FstHierType,
)

__version__ = "0.4.1"
__all__ = [
    "FstReader",
    "FstWriter",
    "FstBlockType",
    "FstScopeType",
    "FstVarType",
    "FstVarDir",
    "FstFileType",
    "FstWriterPackType",
    "FstAttrType",
    "FstMiscType",
    "FstArrayType",
    "FstEnumValueType",
    "FstPackType",
    "FstSupplementalVarType",
    "FstSupplementalDataType",
    "FstHierType",
]

