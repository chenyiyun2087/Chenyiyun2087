"""Project-wide direct-network helpers.

This module disables inherited proxy settings so every workflow in the
repository defaults to direct connections instead of going through a proxy.
"""

from __future__ import annotations

import os
import urllib.request

PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)

NO_PROXY_DEFAULTS = ("*", "localhost", "127.0.0.1", "::1")

_DIRECT_NETWORK_READY = False


def _merge_no_proxy(raw_value: str | None) -> str:
    existing = {item.strip() for item in str(raw_value or "").split(",") if item.strip()}
    return ",".join(sorted(existing | set(NO_PROXY_DEFAULTS)))


def build_direct_network_env(base_env: dict[str, str] | None = None, pythonpath_prefix: str | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)

    no_proxy_value = _merge_no_proxy(env.get("NO_PROXY") or env.get("no_proxy"))
    env["NO_PROXY"] = no_proxy_value
    env["no_proxy"] = no_proxy_value
    env["PROJECT_DIRECT_NETWORK"] = "1"

    if pythonpath_prefix:
        parts: list[str] = []
        current_pythonpath = str(env.get("PYTHONPATH") or "")
        if pythonpath_prefix:
            parts.append(pythonpath_prefix)
        if current_pythonpath:
            parts.extend(item for item in current_pythonpath.split(os.pathsep) if item)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in parts:
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        env["PYTHONPATH"] = os.pathsep.join(deduped)

    return env


def enforce_direct_network() -> None:
    global _DIRECT_NETWORK_READY
    if _DIRECT_NETWORK_READY:
        return

    direct_env = build_direct_network_env()
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = direct_env["NO_PROXY"]
    os.environ["no_proxy"] = direct_env["no_proxy"]
    os.environ["PROJECT_DIRECT_NETWORK"] = "1"

    # urllib should never auto-pick proxy handlers from inherited env vars.
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    _DIRECT_NETWORK_READY = True


def configure_chrome_direct_options(options):
    existing = set(getattr(options, "arguments", []) or [])
    for arg in ("--no-proxy-server", "--proxy-server=direct://", "--proxy-bypass-list=*"):
        if arg not in existing:
            options.add_argument(arg)
    return options
