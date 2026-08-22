import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List


# Only allow file writes inside this directory (your home folder).
SAFE_ROOT = Path("/Users/derek").resolve()


def _now() -> str:
    return datetime.now().strftime("%I:%M %p")


@dataclass
class Tool:
    """A single tool that Mira can call."""

    name: str
    description: str
    parameters: dict
    execute: Callable[[dict], str]


class Tools:
    """Registry of all tools available to Mira."""

    def __init__(self):
        self._registry: Dict[str, Tool] = {}
        self._register_builtins()

    def _register_builtins(self):
        self.register(
            Tool(
                name="time",
                description="Get the current time.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._time,
            )
        )

        self.register(
            Tool(
                name="write_file",
                description="Create or overwrite a file with the given content. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, e.g. 'essay.txt' or 'code/hello.py'"},
                        "content": {"type": "string", "description": "Full content to write"},
                    },
                    "required": ["path", "content"],
                },
                execute=self._write_file,
            )
        )

        self.register(
            Tool(
                name="read_file",
                description="Read the contents of a file. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                execute=self._read_file,
            )
        )

        self.register(
            Tool(
                name="edit_file",
                description="Overwrite an existing file with new content. Same parameters as write_file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                execute=self._write_file,
            )
        )

        self.register(
            Tool(
                name="delete_file",
                description="Delete a file.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                execute=self._delete_file,
            )
        )

        self.register(
            Tool(
                name="list_files",
                description="List files in a directory. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                execute=self._list_files,
            )
        )

        self.register(
            Tool(
                name="execute_command",
                description="Run a shell command and return output. Only use when explicitly asked.",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
                execute=self._execute_command,
            )
        )

    def register(self, tool: Tool):
        self._registry[tool.name] = tool

    def definitions(self) -> List[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._registry.values()
        ]

    def execute(self, name: str, arguments: dict) -> str:
        tool = self._registry.get(name)
        if not tool:
            return f"tool '{name}' not found"
        try:
            return tool.execute(arguments)
        except Exception as e:
            return f"error: {e}"

    def _resolve_path(self, path_str: str) -> Path:
        path = Path(os.path.expanduser(path_str))
        if not path.is_absolute():
            path = Path.home() / "Desktop" / "MiraFiles" / path
        return path

    def _is_safe(self, path: Path) -> bool:
        try:
            return str(path.resolve()).startswith(str(SAFE_ROOT))
        except Exception:
            return False

    def _write_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path} outside allowed directory"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(args["content"], encoding="utf-8")
        temp.replace(path)
        return f"saved {path}"

    def _read_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path}"
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"error reading {path}: {e}"

    def _delete_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path}"
        try:
            path.unlink()
            return f"deleted {path}"
        except Exception as e:
            return f"error deleting {path}: {e}"

    def _list_files(self, args: dict) -> str:
        dir_path = self._resolve_path(args["path"])
        if not self._is_safe(dir_path):
            return f"refused: {dir_path}"
        try:
            entries = sorted(dir_path.iterdir())
            return "\n".join(str(e.name) for e in entries) or "(empty)"
        except Exception as e:
            return f"error listing {dir_path}: {e}"

    def _execute_command(self, args: dict) -> str:
        command = args["command"]
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            return output[:2000] or "(no output)"
        except Exception as e:
            return f"command failed: {e}"

    def _time(self, args: dict) -> str:
        return _now()
