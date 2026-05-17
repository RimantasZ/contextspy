# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


_DEFAULT_DIR = Path.home() / ".contextspy"


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
    db_path: Path = field(default_factory=lambda: _DEFAULT_DIR / "contextspy.db")


@dataclass
class ReverseTarget:
    """A local LLM server to proxy in reverse mode."""
    name: str                   # human label, e.g. "llama-server"
    listen_port: int            # port contextspy listens on, e.g. 8889
    target_url: str             # upstream URL, e.g. "http://127.0.0.1:8080"
    provider: str = "openai"    # parser to use: "openai" | "anthropic" | "ollama"


@dataclass
class Settings:
    proxy: ProxySettings = field(default_factory=ProxySettings)
    web: WebSettings = field(default_factory=WebSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    extra_hosts: list[str] = field(default_factory=list)
    reverse_targets: list[ReverseTarget] = field(default_factory=list)
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
            if "reverse_targets" in data:
                for rt in data["reverse_targets"]:
                    settings.reverse_targets.append(
                        ReverseTarget(
                            name=rt["name"],
                            listen_port=int(rt["listen_port"]),
                            target_url=rt["target_url"],
                            provider=rt.get("provider", "openai"),
                        )
                    )
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

# Uncomment and edit to enable local reverse-proxy mode.
# Each [[reverse_targets]] block defines one local LLM server to intercept.
# [[reverse_targets]]
# name        = "llama-server"   # display label
# listen_port = 8889             # port contextspy listens on
# target_url  = "http://127.0.0.1:8080"  # where your server actually runs
# provider    = "openai"         # parser: "openai" | "anthropic" | "ollama"
""",
                encoding="utf-8",
            )
