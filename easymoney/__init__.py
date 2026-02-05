"""兼容包：对外转发至 Eastmoney。"""

from Eastmoney import DuokongSnapshot, fetch_duokong_snapshot

__all__ = ["EastmoneyController", "DuokongSnapshot", "fetch_duokong_snapshot"]


def __getattr__(name):
    if name == "EastmoneyController":
        from Eastmoney import EastmoneyController

        return EastmoneyController
    raise AttributeError(name)
