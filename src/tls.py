"""HTTP client that trusts a private CA certificate, for course endpoints served with their own .crt file."""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def http_client(ca_cert: str = "") -> Optional["httpx.Client"]:  # noqa: F821
    """Return an httpx client that verifies the server against the given .crt file, or None to use the default trust store.

    The path must be exact; nothing searches for the file. A leading ~ or $HOME is expanded.
    """
    if not ca_cert.strip():
        return None
    import os

    import httpx

    path = Path(os.path.expandvars(os.path.expanduser(ca_cert.strip())))
    if not path.is_file():
        raise FileNotFoundError(f"The CA certificate file was not found: {path}. Set the exact path of the .crt file in the .env file.")
    return httpx.Client(verify=str(path))


def async_http_client(ca_cert: str = "") -> Optional["httpx.AsyncClient"]:  # noqa: F821
    """Same as http_client, for libraries that call the endpoint asynchronously (the Ragas judge does)."""
    sync = http_client(ca_cert)
    if sync is None:
        return None
    import os
    from pathlib import Path as _P

    import httpx

    return httpx.AsyncClient(verify=str(_P(os.path.expandvars(os.path.expanduser(ca_cert.strip())))))
