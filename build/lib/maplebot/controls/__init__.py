"""Input layer: Actions -> key presses, with guaranteed release."""
from .backends import (
    FakeBackend,
    InputBackend,
    SendInputBackend,
    available_keys,
    default_backend,
    normalize_key,
)
from .controller import Controller

__all__ = [
    "Controller",
    "InputBackend",
    "FakeBackend",
    "SendInputBackend",
    "default_backend",
    "available_keys",
    "normalize_key",
]
