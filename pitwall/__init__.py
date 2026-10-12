"""Pitwall — iRacing engineer/spotter companion.

Layers (strict, one-way dependencies):
    sources   -> raw iRacing variables (live shared memory, memory dump, or fake)
    normalize -> raw variables into protocol messages (docs/PROTOCOL.md)
    trackmap  -> derived track outline from a recorded lap
    recorder / analyze -> phase-1 truth harness
    bridge    -> streams messages to the relay, receives commands
    commands  -> the only module allowed to send anything INTO iRacing
"""
__version__ = "0.1.0"
