"""Runtime settings: .env + environment variables + YAML config, resolved against the project root."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def find_root(start: Path | None = None) -> Path:
    """The project root is the nearest folder (upwards) that has config/platforms.yaml."""
    here = (start or Path.cwd()).resolve()
    for p in [here, *here.parents]:
        if (p / "config" / "platforms.yaml").is_file():
            return p
    raise SystemExit("growthengine: run this from the project folder (no config/platforms.yaml found)")


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    platforms: dict
    formats: dict
    mix: dict
    schedule: dict
    brand: dict
    claims: dict
    rights: dict
    utm: dict
    prices: dict

    def platform(self, name: str) -> dict:
        return self.platforms[name]

    def fmt(self, name: str) -> dict:
        return self.formats[name]


@dataclass
class Settings:
    root: Path
    config: Config
    llm: str = "mock"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    concurrency: int = 4
    data_dir: Path = field(default=Path("data"))
    content_dir: Path = field(default=Path("content"))
    out_dir: Path = field(default=Path("out"))
    log_dir: Path = field(default=Path("logs"))
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    whisper_model: str = "small"
    fault: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "engine.db"

    def rel(self, p: Path) -> str:
        """Path relative to the project root, with forward slashes (for CSV, logs and rights matching)."""
        try:
            return Path(p).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return Path(p).resolve().as_posix()


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config(root: Path) -> Config:
    c = root / "config"
    platforms = _yaml(c / "platforms.yaml")
    utm = _yaml(c / "utm.yaml")
    if os.environ.get("UTM_BASE_URL"):
        utm["base_url"] = os.environ["UTM_BASE_URL"]
    return Config(
        platforms=platforms["platforms"],
        formats=platforms["formats"],
        mix=_yaml(c / "mix.yaml"),
        schedule=_yaml(c / "schedule.yaml"),
        brand=_yaml(c / "brand.yaml"),
        claims=_yaml(c / "claims.yaml"),
        rights=_yaml(c / "rights.yaml"),
        utm=utm,
        prices=_yaml(c / "prices.yaml"),
    )


def load_settings(root: Path | None = None, **overrides) -> Settings:
    root = find_root(root)
    load_dotenv(root / ".env")
    env = os.environ

    def d(name: str, default: str) -> Path:
        p = Path(env.get(name, default))
        return p if p.is_absolute() else root / p

    llm = env.get("GE_LLM", "").strip().lower()
    if not llm:
        llm = "claude" if env.get("ANTHROPIC_API_KEY") else "mock"
    s = Settings(
        root=root,
        config=load_config(root),
        llm=llm,
        model=env.get("LLM_MODEL", "claude-opus-5-5"),
        effort=env.get("LLM_EFFORT", "medium"),
        concurrency=int(env.get("LLM_CONCURRENCY", "4")),
        data_dir=d("GE_DATA_DIR", "data"),
        content_dir=d("GE_CONTENT_DIR", "content"),
        out_dir=d("GE_OUT_DIR", "out"),
        log_dir=d("GE_LOG_DIR", "logs"),
        ffmpeg=env.get("FFMPEG", "ffmpeg"),
        ffprobe=env.get("FFPROBE", "ffprobe"),
        whisper_model=env.get("WHISPER_MODEL", "small"),
        fault=env.get("GE_FAULT", ""),
    )
    for k, v in overrides.items():
        if v is not None:
            setattr(s, k, v)
    for p in (s.data_dir, s.content_dir, s.out_dir, s.log_dir):
        p.mkdir(parents=True, exist_ok=True)
    return s
