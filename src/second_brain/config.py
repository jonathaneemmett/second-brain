import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    vault: Path
    api_key: str
    model: str
    base_url: str
    max_input_chars: int
    lookback_hours: int
    notify_url: str
    mcp_port: int
    mcp_allowed_hosts: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        hosts = os.environ.get("MCP_ALLOWED_HOSTS", "")
        return cls(
            vault=Path(os.environ.get("VAULT_PATH", "vault")).expanduser().resolve(),
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=os.environ.get("OPENROUTER_MODEL", ""),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            max_input_chars=int(os.environ.get("DREAM_MAX_INPUT_CHARS", "60000")),
            lookback_hours=int(os.environ.get("DREAM_LOOKBACK_HOURS", "30")),
            notify_url=os.environ.get("NOTIFY_URL", ""),
            mcp_port=int(os.environ.get("MCP_PORT", "8765")),
            mcp_allowed_hosts=tuple(h.strip() for h in hosts.split(",") if h.strip()),
        )
