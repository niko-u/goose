"""Environment-based configuration.

All settings come from LABVAULT_* environment variables so the same code runs
under launchd, Docker, or a plain shell. Nothing here ever points at a cloud
service by default — the LLM endpoint must be a loopback/private address unless
the user explicitly opts out via LABVAULT_ALLOW_REMOTE_LLM=1.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("LABVAULT_DATA_DIR", "~/.labvault")).expanduser())
    # LLM
    llm_backend: str = field(default_factory=lambda: os.environ.get("LABVAULT_LLM_BACKEND", "ollama"))  # "ollama" | "openai" | "none"
    llm_base_url: str = field(default_factory=lambda: os.environ.get("LABVAULT_LLM_BASE_URL", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("LABVAULT_LLM_MODEL", "gemma3"))
    llm_timeout: float = field(default_factory=lambda: float(os.environ.get("LABVAULT_LLM_TIMEOUT", "300")))
    allow_remote_llm: bool = field(default_factory=lambda: _env_bool("LABVAULT_ALLOW_REMOTE_LLM", False))
    # Storage policy
    keep_originals: bool = field(default_factory=lambda: _env_bool("LABVAULT_KEEP_ORIGINALS", False))
    # Web
    host: str = field(default_factory=lambda: os.environ.get("LABVAULT_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("LABVAULT_PORT", "8087")))
    # Watch folder
    watch_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["LABVAULT_WATCH_DIR"]).expanduser() if os.environ.get("LABVAULT_WATCH_DIR") else None
    )

    def __post_init__(self) -> None:
        if not self.llm_base_url:
            self.llm_base_url = "http://localhost:11434" if self.llm_backend == "ollama" else "http://localhost:1234/v1"

    @property
    def db_path(self) -> Path:
        return Path(os.environ.get("LABVAULT_DB_PATH", str(self.data_dir / "labvault.db"))).expanduser()

    @property
    def originals_dir(self) -> Path:
        return self.data_dir / "originals"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.keep_originals:
            self.originals_dir.mkdir(parents=True, exist_ok=True)

    def check_llm_privacy(self) -> None:
        """Refuse to talk to a non-private LLM endpoint unless explicitly allowed.

        This is the core privacy guarantee: lab report text is only ever sent to
        the configured endpoint, and that endpoint must resolve to a loopback or
        RFC1918/link-local address.
        """
        if self.llm_backend == "none" or self.allow_remote_llm:
            return
        host = urlparse(self.llm_base_url).hostname or ""
        if host in ("localhost", ""):
            return
        try:
            addrs = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            # Unresolvable now (e.g. offline). The runtime call will fail loudly instead.
            return
        for addr in addrs:
            ip = ipaddress.ip_address(addr.split("%")[0])
            if not (ip.is_private or ip.is_loopback or ip.is_link_local):
                raise PrivacyError(
                    f"LLM endpoint {self.llm_base_url!r} resolves to public address {addr}. "
                    "LabVault refuses to send lab report text to non-private hosts. "
                    "Set LABVAULT_ALLOW_REMOTE_LLM=1 only if you really mean it."
                )


class PrivacyError(RuntimeError):
    pass


def get_settings() -> Settings:
    return Settings()
