"""Ask Claude to write the service, parse its file blocks, and write them safely to disk."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from . import prompts

DEFAULT_MODEL = os.getenv("APIFORGE_MODEL", "claude-sonnet-5-5")
GENERATED_FILES = ("upstream.py", "main.py", "smoke_tests.json", "README.md")
TEMPLATES = Path(__file__).parent / "templates"

_FILE_BLOCK = re.compile(r'<file path="([^"]+)">\n?(.*?)\n?</file>', re.S)


def parse_files(text: str) -> dict[str, str]:
    """Extract <file path="..."> blocks. Only whitelisted names are accepted, so the model can
    never write outside the service folder."""
    files = {}
    for path, body in _FILE_BLOCK.findall(text):
        name = path.strip()
        if name in GENERATED_FILES:
            files[name] = body.strip("\n") + "\n"
    return files


class Generator:
    """Holds the conversation so repair turns keep the full capture context."""

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 32000):
        from anthropic import Anthropic  # imported lazily so tests don't need a key

        self.client = Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.messages: list[dict] = []

    def _ask(self, content: str) -> str:
        self.messages.append({"role": "user", "content": content})
        # Streaming avoids HTTP timeouts on long generations.
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=prompts.SYSTEM,
            messages=self.messages,
        ) as stream:
            message = stream.get_final_message()
        text = "".join(b.text for b in message.content if b.type == "text")
        self.messages.append({"role": "assistant", "content": text})
        return text

    def generate(self, name: str, goal: str, start_url: str | None, endpoints_json: str) -> dict[str, str]:
        text = self._ask(prompts.build_user_prompt(name, goal, start_url, endpoints_json))
        return _require_all(parse_files(text))

    def repair(self, failure_log: str) -> dict[str, str]:
        text = self._ask(prompts.build_repair_prompt(failure_log[-12000:]))
        return _require_all(parse_files(text))


def _require_all(files: dict[str, str]) -> dict[str, str]:
    missing = [f for f in GENERATED_FILES if f not in files]
    if missing:
        raise RuntimeError(f"Model output was missing files: {', '.join(missing)}")
    return files


def write_service(out_dir: Path, files: dict[str, str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8")
    # Fixed scaffolding: same for every generated API, so it is never the LLM's job to get right.
    for tpl in ("Dockerfile", "requirements.txt", ".dockerignore"):
        shutil.copy(TEMPLATES / tpl, out_dir / tpl)
