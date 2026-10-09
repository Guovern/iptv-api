import os
import subprocess
import sys


_PROXY_ENV_NAMES = {"http_proxy", "https_proxy", "all_proxy", "ftp_proxy"}


def direct_network_env(environ=None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    return {
        key: value
        for key, value in source.items()
        if key.lower() not in _PROXY_ENV_NAMES
    }


def no_window_process_kwargs() -> dict[str, int]:
    if sys.platform != "win32":
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }
