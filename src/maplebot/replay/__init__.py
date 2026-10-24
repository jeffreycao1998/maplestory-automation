"""Replay and synthetic data: stand-ins for the live game. Owner: test-harness-engineer.

Import submodules directly (``maplebot.replay.synthetic``, ``maplebot.replay.folder_capture``);
this package re-exports the common names.
"""
from .folder_capture import EndOfRecording, FolderCapture, SequenceCapture
from .synthetic import SceneSequence, make_scene, mob_sprite, name_tag_sprite, settings_for_scene

__all__ = ["EndOfRecording", "FolderCapture", "SequenceCapture", "SceneSequence",
           "make_scene", "mob_sprite", "name_tag_sprite", "settings_for_scene"]
