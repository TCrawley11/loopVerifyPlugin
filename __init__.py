"""Hermes loads this directory as the `loopverify` plugin. The code lives in `plugin/`."""

from .plugin import register

__all__ = ["register"]
