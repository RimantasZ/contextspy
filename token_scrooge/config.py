from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


_DEFAULT_DIR = Path.home() / ".token-scrooge"


@dataclass
class ProxySettings:
    port: int = 8888
    bind_addr: str = "127.0.0.1"


@dataclass
class WebSettings:
    port: int = 5173
    bind_addr: str = "127.0.0.1"


@dataclass
class StorageSettings:
    db_path: Path = field(default_factory=lambda: _DEFAULT_DIR / "token-scrooge.db")


@dataclass
class Settings:
    proxy: ProxySettings = field(default_factory=ProxySettings)
    web: WebSettings = field(default_factory=WebSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    extra_hosts: list[str] = field(default_factory=list)
    config_dir: Path = field(default_factory=lambda: _DEFAULT_DIR)

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        settings = cls()
        path = config_path or (_DEFAULT_DIR / "config.toml")
        if path.exists():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            if "proxy" in data:
                p = data["proxy"]
                settings.proxy.port = p.get("port", settings.proxy.port)
                settings.proxy.bind_addr = p.get("bind_addr", settings.proxy.bind_addr)
            if "web" in data:
                w = data["web"]
                settings.web.port = w.get("port", settings.web.port)
                settings.web.bind_addr = w.get("bind_addr", settings.web.bind_addr)
            if "storage" in data:
                s = data["storage"]
                if "db_path" in s:
                    settings.storage.db_path = Path(s["db_path"]).expanduser()
            if "intercepted_hosts" in data:
                settings.extra_hosts = data["intercepted_hosts"].get("extra_hosts", [])
        return settings

    def ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.storage.db_path.parent.mkdir(parents=True, exist_ok=True)

    def write_defaults(self) -> None:
        self.ensure_dirs()
        config_path = self.config_dir / "config.toml"
        if not config_path.exists():
            db_path_toml = str(self.storage.db_path).replace("\\", "/")
            config_path.write_text(
                f"""[proxy]
port = {self.proxy.port}
bind_addr = "{self.proxy.bind_addr}"

[web]
port = {self.web.port}
bind_addr = "{self.web.bind_addr}"

[storage]
db_path = "{db_path_toml}"

[intercepted_hosts]
# Add extra hosts if needed (besides the built-in list)
extra_hosts = []
""",
                encoding="utf-8",
            )
