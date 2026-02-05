"""Eastmoney data access package."""

from .duokong_scanner import DuokongSnapshot, fetch_duokong_snapshot

__all__ = ["EastmoneyController", "DuokongSnapshot", "fetch_duokong_snapshot"]


def __getattr__(name):
    if name == "EastmoneyController":
        from .EastmoneyController import EastmoneyController

        return EastmoneyController
    raise AttributeError(name)
