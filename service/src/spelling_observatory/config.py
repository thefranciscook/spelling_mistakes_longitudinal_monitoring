"""Application paths and configuration.

Configuration lives in a TOML file in the application home directory. Only the values in
``ClassifierConfig`` and ``LanguageConfig`` influence classification output; they are hashed
into the classifier fingerprint so a threshold change can never silently mix with older data.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def app_home() -> Path:
    env = os.environ.get("SPELLING_OBSERVATORY_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SpellingObservatory"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "spelling-observatory"


@dataclass
class StorageConfig:
    # "sqlcipher": encrypted database, key in the OS keychain (macOS Keychain via `keyring`).
    # "none": plain SQLite (file mode 0600). Intended for tests and for users relying on FileVault.
    encryption: str = "sqlcipher"
    database: str = "observatory.db"


@dataclass
class LanguageConfig:
    # Language identification combines two signals, both computed per sentence:
    #  1. lingua, restricted to the user's two languages (English vs Hungarian);
    #  2. a lexical vote: the share of tokens that are English words vs Hungarian words.
    # Other languages (German, French, ...) are caught by the lexical vote.
    languages: list[str] = field(default_factory=lambda: ["ENGLISH", "HUNGARIAN"])
    min_english_confidence: float = 0.50
    min_english_word_ratio: float = 0.60
    max_hungarian_word_ratio: float = 0.20
    # Sentences with fewer tokens are judged together with their paragraph.
    sentence_min_words: int = 3
    # If more than this fraction of a paragraph's words fall in non-English sentences, the
    # whole paragraph is treated as mixed-language and excluded.
    max_foreign_fraction: float = 0.25


@dataclass
class ClassifierConfig:
    min_error_token_length: int = 3
    max_edit_distance: int = 2
    # Noisy-channel prior: each extra edit makes a candidate this many times less likely.
    distance_penalty: float = 1000.0
    # Deterministic acceptance requires the best candidate to beat the runner-up by this factor.
    dominance_ratio: float = 20.0
    # Candidates rarer than this (SymSpell corpus counts) are ignored.
    min_candidate_count: int = 10_000
    # Messages longer than this many characters are treated as suspected pastes.
    max_message_chars: int = 4000
    # Word n-gram size used to detect text copied from the assistant's earlier replies.
    assistant_overlap_ngram: int = 6


@dataclass
class LLMConfig:
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b-instruct-q4_K_M"
    timeout_seconds: float = 60.0
    workers: int = 2


@dataclass
class MasteredConfig:
    min_baseline_observations: int = 20
    min_baseline_accuracy: float = 0.95
    min_recent_observations: int = 10


@dataclass
class ReportConfig:
    timezone: str = "America/Toronto"
    # Fixed historical baseline. Empty = first `default_baseline_days` of data.
    baseline_start: str = ""
    baseline_end: str = ""
    default_baseline_days: int = 365
    recent_days: int = 90
    # Periods with fewer eligible words are shown but marked as insufficient.
    min_period_words: int = 1000
    bootstrap_iterations: int = 2000


@dataclass
class Config:
    storage: StorageConfig = field(default_factory=StorageConfig)
    language: LanguageConfig = field(default_factory=LanguageConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    mastered: MasteredConfig = field(default_factory=MasteredConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    home: Path = field(default_factory=app_home)

    @property
    def database_path(self) -> Path:
        return self.home / self.storage.database

    @property
    def resources_dir(self) -> Path:
        return self.home / "resources"

    @property
    def user_exclusions_path(self) -> Path:
        return self.home / "exclusions.txt"

    @property
    def config_path(self) -> Path:
        return self.home / "config.toml"


def _apply(section_obj, values: dict) -> None:
    names = {f.name for f in dataclasses.fields(section_obj)}
    for key, value in values.items():
        if key not in names:
            raise ValueError(f"Unknown configuration key: {key}")
        setattr(section_obj, key, value)


def load_config(home: Path | None = None) -> Config:
    cfg = Config(home=home or app_home())
    if cfg.config_path.exists():
        data = tomllib.loads(cfg.config_path.read_text())
        for section, values in data.items():
            if not hasattr(cfg, section) or section == "home":
                raise ValueError(f"Unknown configuration section: [{section}]")
            _apply(getattr(cfg, section), values)
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_config(cfg: Config) -> str:
    lines = ["# Personal Spelling Observatory configuration", ""]
    for f in dataclasses.fields(cfg):
        if f.name == "home":
            continue
        section = getattr(cfg, f.name)
        lines.append(f"[{f.name}]")
        for sf in dataclasses.fields(section):
            lines.append(f"{sf.name} = {_toml_value(getattr(section, sf.name))}")
        lines.append("")
    return "\n".join(lines)


def ensure_home(cfg: Config) -> None:
    cfg.home.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.home, 0o700)
    if not cfg.config_path.exists():
        cfg.config_path.write_text(dump_config(cfg))
    if not cfg.user_exclusions_path.exists():
        cfg.user_exclusions_path.write_text(
            "# Personal exclusion dictionary: one word per line. These are never counted as\n"
            "# spelling errors and are not counted as eligible words.\n"
        )
