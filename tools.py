import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
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
    risky: bool = False


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
                description="Edit an EXISTING file by fully replacing its content. The file must already exist; if it doesn't, use write_file instead.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path of an existing file to overwrite"},
                        "content": {"type": "string", "description": "New full content"},
                    },
                    "required": ["path", "content"]},
                execute=self._edit_file,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="delete_file",
                description="Permanently delete a file. Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]},
                execute=self._delete_file,
                risky=True,
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
                description="Run a shell command and return output. Only use when the user explicitly asks, and keep it safe.",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]},
                execute=self._execute_command,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="open_file",
                description="Open a local file with the default application. Path is relative to ~ unless absolute.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "File path to open"}},
                    "required": ["path"],
                },
                execute=self._open_file,
            )
        )

        self.register(
            Tool(
                name="open_website",
                description="Open a URL in the default browser.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "Full URL to open"}},
                    "required": ["url"],
                },
                execute=self._open_website,
            )
        )

        self.register(
            Tool(
                name="web_search",
                description="Open a Bing web search in the default browser.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Search query"}},
                    "required": ["query"],
                },
                execute=self._web_search,
            )
        )

        self.register(
            Tool(
                name="read_website",
                description="Fetch the text content of a webpage for research.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                    "required": ["url"],
                },
                execute=self._read_website,
            )
        )

        self.register(
            Tool(
                name="system_info",
                description="Get basic system information (OS, version, architecture, hostname).",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._system_info,
            )
        )

        self.register(
            Tool(
                name="open_app",
                description="Open a macOS application by name.",
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "App name, e.g. 'Safari' or 'Visual Studio Code'"}},
                    "required": ["name"],
                },
                execute=self._open_app,
            )
        )

        self.register(
            Tool(
                name="close_app",
                description="Quit or force-close an application. Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {
                        "app": {"type": "string", "description": "Application name"},
                        "force": {"type": "boolean", "description": "Use killall instead of graceful quit"},
                    },
                    "required": ["app"]},
                execute=self._close_app,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="toggle_wifi",
                description="Toggle WiFi on or off. Only use when the user explicitly asks.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._toggle_wifi,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="toggle_airdrop",
                description="Toggle AirDrop discoverability off/everyone. Only use when the user explicitly asks.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._toggle_airdrop,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="notify",
                description="Show a macOS notification.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["title", "message"],
                },
                execute=self._notify,
            )
        )

        self.register(
            Tool(
                name="type_text",
                description="Type text into the currently focused application. Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to type"}},
                    "required": ["text"]},
                execute=self._type_text,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="press_key",
                description="Press a key in the currently focused application (return, tab, escape, etc.). Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": "Key name"}},
                    "required": ["key"]},
                execute=self._press_key,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="get_volume",
                description="Get the current system output volume percentage.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._get_volume,
            )
        )

        self.register(
            Tool(
                name="set_volume",
                description="Set the system output volume (0-100). Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {"level": {"type": "integer", "description": "Volume level 0-100"}},
                    "required": ["level"]},
                execute=self._set_volume,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="move_mouse",
                description="Move the mouse cursor by (dx, dy) pixels. Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {
                        "dx": {"type": "integer"},
                        "dy": {"type": "integer"},
                    },
                    "required": ["dx", "dy"]},
                execute=self._move_mouse,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="shake_mouse",
                description="Jitter the mouse cursor around for a moment. Only use when the user explicitly asks.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._shake_mouse,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="click_mouse",
                description="Click the mouse at optional (x, y) coordinates. Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "clicks": {"type": "integer"},
                    },
                    "required": []},
                execute=self._click_mouse,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="get_mouse_position",
                description="Get the current mouse cursor coordinates.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._get_mouse_position,
            )
        )

        self.register(
            Tool(
                name="get_clipboard",
                description="Read the current clipboard text.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._get_clipboard,
            )
        )

        self.register(
            Tool(
                name="set_clipboard",
                description="Set the clipboard text.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to copy"}},
                    "required": ["text"],
                },
                execute=self._set_clipboard,
            )
        )

        self.register(
            Tool(
                name="close_front_window",
                description="Close the frontmost window (Command+W). Useful for playful pranks. Will not close the terminal Mira is running in.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._close_front_window,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="minimize_front_window",
                description="Minimize the frontmost window. Will not minimize Mira's terminal.",
                parameters={"type": "object", "properties": {}, "required": []},
                execute=self._minimize_front_window,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="resize_window",
                description="Resize/reposition the front window of an application. Only use when the user explicitly asks.",
                parameters={
                    "type": "object",
                    "properties": {
                        "app": {"type": "string", "description": "Application name"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["app"]},
                execute=self._resize_window,
                risky=True,
            )
        )

        self.register(
            Tool(
                name="ask_chatgpt",
                description="Ask ChatGPT a question and get a response. Use this for research or when you need another opinion.",
                parameters={
                    "type": "object",
                    "properties": {"question": {"type": "string", "description": "Full question to ask"}},
                    "required": ["question"],
                },
                execute=self._ask_chatgpt,
            )
        )

    def register(self, tool: Tool):
        self._registry[tool.name] = tool

    def is_risky(self, name: str) -> bool:
        tool = self._registry.get(name)
        return tool.risky if tool else False

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

    def _edit_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path} outside allowed directory"
        if not path.exists():
            return f"error: {path} does not exist. Use write_file to create it."
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(args["content"], encoding="utf-8")
        temp.replace(path)
        return f"edited {path}"

    def _read_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path}"
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                parts = []
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        parts.append(page_text)
                text = "\n".join(parts).strip()
                if not text:
                    return "pdf exists but has no extractable text"
                max_chars = 8000
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n\n[truncated: pdf has more text]"
                return text
            except ImportError:
                return "pdf found but pypdf is not installed. run: pip3 install pypdf"
            except Exception as e:
                return f"could not read pdf: {e}"
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

    def _open_file(self, args: dict) -> str:
        path = Path(os.path.expanduser(args["path"])).resolve()
        safe_root = Path("/Users/derek").resolve()
        if not str(path).startswith(str(safe_root)):
            return f"refused: {path} outside allowed directory"
        if not path.exists():
            return f"file not found: {path}"
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=True)
            else:
                webbrowser.open(path.as_uri())
            return f"opened {path}"
        except Exception as e:
            return f"error opening file: {e}"

    def _open_website(self, args: dict) -> str:
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", url], check=True)
            else:
                webbrowser.open(url)
            return f"opened {url}"
        except Exception as e:
            return f"error opening website: {e}"

    def _web_search(self, args: dict) -> str:
        query = args["query"]
        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/search?q={encoded}"
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", url], check=True)
            else:
                webbrowser.open(url)
            return f"searching: {query}"
        except Exception as e:
            return f"error searching: {e}"

    def _read_website(self, args: dict) -> str:
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return f"error fetching {url}: {e}"
        try:
            from html.parser import HTMLParser

            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.texts = []
                    self.skip = False

                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self.skip = True

                def handle_endtag(self, tag):
                    if tag in ("script", "style", "nav", "footer", "header"):
                        self.skip = False

                def handle_data(self, data):
                    if not self.skip:
                        self.texts.append(data)

            parser = TextExtractor()
            parser.feed(html)
            text = " ".join(parser.texts)
            text = " ".join(text.split())
            if not text.strip():
                return "page loaded but no readable text found"
            limit = 4000
            if len(text) > limit:
                text = text[:limit] + "..."
            return text
        except Exception as e:
            return f"fetched {url} but could not parse text: {e}"

    def _system_info(self, args: dict) -> str:
        info = {
            "os": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "hostname": socket.gethostname(),
        }
        return "\n".join(f"{k}: {v}" for k, v in info.items())

    def _open_app(self, args: dict) -> str:
        name = args["name"]
        try:
            subprocess.run(["open", "-a", name], check=True)
            return f"opened {name}"
        except Exception as e:
            return f"error opening app: {e}"

    def _close_app(self, args: dict) -> str:
        app = args.get("app", "")
        force = str(args.get("force", "false")).lower() in ("true", "1", "yes")
        if not app:
            return "no app name given"
        try:
            if force:
                subprocess.run(["killall", app], check=False, capture_output=True, text=True)
                return f"force-closed {app}"
            script = f'quit application "{app}"'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return f"closed {app}"
        except Exception as e:
            return f"close failed: {e}"

    def _toggle_wifi(self, args: dict) -> str:
        try:
            for interface in ["en0", "en1", "Wi-Fi", "AirPort"]:
                try:
                    result = subprocess.run(["networksetup", "-getairportpower", interface], capture_output=True, text=True, check=False)
                    if "Error" not in result.stdout and "You cannot" not in result.stdout:
                        current = result.stdout.lower()
                        new_state = "off" if "on" in current else "on"
                        subprocess.run(["networksetup", "-setairportpower", interface, new_state], capture_output=True, text=True, check=False)
                        return f"wifi toggled to {new_state} ({interface})"
                except Exception:
                    continue
            return "wifi toggle failed: could not find interface"
        except Exception as e:
            return f"wifi toggle failed: {e}"

    def _toggle_airdrop(self, args: dict) -> str:
        try:
            result = subprocess.run(["defaults", "read", "com.apple.sharingd", "DiscoverableMode"], capture_output=True, text=True, check=False)
            try:
                current = int(result.stdout.strip())
            except Exception:
                current = 0
            new_mode = 0 if current > 0 else 2
            subprocess.run(["defaults", "write", "com.apple.sharingd", "DiscoverableMode", "-int", str(new_mode)], check=True, capture_output=True, text=True)
            mode_name = "off" if new_mode == 0 else "everyone"
            return f"airdrop toggled to {mode_name}"
        except Exception as e:
            return f"airdrop toggle failed: {e}"

    def _notify(self, args: dict) -> str:
        title = args["title"].replace('"', '\\"')
        message = args["message"].replace('"', '\\"')
        try:
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], check=True)
            return "notification sent"
        except Exception as e:
            return f"notification failed: {e}"

    def _type_text(self, args: dict) -> str:
        text = args["text"]
        try:
            try:
                import pyautogui
                pyautogui.typewrite(text, interval=0.01)
                return f"typed: {text[:50]}{'...' if len(text) > 50 else ''}"
            except Exception:
                pass
            try:
                from pynput.keyboard import Controller
                Controller().type(text)
                return f"typed: {text[:50]}{'...' if len(text) > 50 else ''}"
            except Exception:
                pass
            safe = text.replace('"', '\\"')
            script = f'tell application "System Events" to keystroke "{safe}"'
            subprocess.run(["osascript", "-e", script], check=True)
            return f"typed: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"typing failed: {e}"

    def _press_key(self, args: dict) -> str:
        key = args["key"].lower()
        try:
            try:
                import pyautogui
                pyautogui.press(key)
                return f"pressed {key}"
            except Exception:
                pass
            try:
                from pynput.keyboard import Controller, Key
                kb = Controller()
                media_map = {
                    "play_pause": Key.media_play_pause,
                    "playpause": Key.media_play_pause,
                    "prevtrack": Key.media_previous,
                    "nexttrack": Key.media_next,
                }
                pkey = media_map.get(key, getattr(Key, key, key))
                kb.press(pkey)
                kb.release(pkey)
                return f"pressed {key}"
            except Exception:
                pass
            key_map = {
                "return": "key code 36",
                "enter": "key code 36",
                "tab": "key code 48",
                "escape": "key code 53",
                "esc": "key code 53",
                "space": "key code 49",
                "backspace": "key code 51",
                "delete": "key code 51",
                "up": "key code 126",
                "down": "key code 125",
                "left": "key code 123",
                "right": "key code 124",
            }
            action = key_map.get(key)
            if not action:
                return f"unknown key: {key}"
            script = f'tell application "System Events" to {action}'
            subprocess.run(["osascript", "-e", script], check=True)
            return f"pressed {key}"
        except Exception as e:
            return f"key press failed: {e}"

    def _get_volume(self, args: dict) -> str:
        try:
            result = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True, check=True)
            return f"volume is {result.stdout.strip()}%"
        except Exception as e:
            return f"volume check failed: {e}"

    def _set_volume(self, args: dict) -> str:
        try:
            level = int(args.get("level", 50))
            level = max(0, min(100, level))
            subprocess.run(["osascript", "-e", f"set volume output volume {level}"], check=True, capture_output=True, text=True)
            return f"volume set to {level}%"
        except Exception as e:
            return f"volume set failed: {e}"

    def _move_mouse(self, args: dict) -> str:
        try:
            import pyautogui
            dx = int(args.get("dx", 0))
            dy = int(args.get("dy", 0))
            x, y = pyautogui.position()
            pyautogui.moveTo(x + dx, y + dy, duration=0.2)
            return f"moved mouse by ({dx}, {dy})"
        except Exception as e:
            return f"move mouse failed: {e}"

    def _shake_mouse(self, args: dict) -> str:
        try:
            import pyautogui
            import random as _random
            screen_w, screen_h = pyautogui.size()
            for _ in range(40):
                x = _random.randint(0, screen_w)
                y = _random.randint(0, screen_h)
                pyautogui.moveTo(x, y, duration=0.05)
            return "shook mouse all over the screen"
        except Exception as e:
            return f"shake failed: {e}"

    def _click_mouse(self, args: dict) -> str:
        try:
            import pyautogui
            x = args.get("x")
            y = args.get("y")
            clicks = int(args.get("clicks", 1))
            if x is not None and y is not None:
                pyautogui.click(int(x), int(y), clicks=clicks)
            else:
                pyautogui.click(clicks=clicks)
            return f"clicked {clicks} time(s)"
        except Exception as e:
            return f"click failed: {e}"

    def _get_mouse_position(self, args: dict) -> str:
        try:
            import pyautogui
            x, y = pyautogui.position()
            return f"mouse at ({x}, {y})"
        except Exception as e:
            return f"mouse position failed: {e}"

    def _get_clipboard(self, args: dict) -> str:
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
            return result.stdout
        except Exception as e:
            return f"clipboard read failed: {e}"

    def _set_clipboard(self, args: dict) -> str:
        try:
            text = args.get("text", "")
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            return "clipboard set"
        except Exception as e:
            return f"clipboard set failed: {e}"

    def _close_front_window(self, args: dict) -> str:
        try:
            front_app = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'], capture_output=True, text=True, check=True).stdout.strip()
            if front_app.lower() in ("terminal", "iterm", "ghostty"):
                return "refused: front window is the terminal"
            subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "w" using command down'], check=True)
            return f"closed front window of {front_app}"
        except Exception as e:
            return f"close front window failed: {e}"

    def _minimize_front_window(self, args: dict) -> str:
        try:
            front_app = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'], capture_output=True, text=True, check=True).stdout.strip()
            if front_app.lower() in ("terminal", "iterm", "ghostty"):
                return "refused: front window is the terminal"
            subprocess.run(["osascript", "-e", 'tell application "System Events" to keystroke "m" using command down'], check=True)
            return f"minimized front window of {front_app}"
        except Exception as e:
            return f"minimize failed: {e}"

    def _resize_window(self, args: dict) -> str:
        app = args.get("app", "Terminal")
        width = int(args.get("width", 800))
        height = int(args.get("height", 600))
        x = int(args.get("x", 0))
        y = int(args.get("y", 0))
        try:
            script = f'tell application "{app}"\n    set bounds of front window to {{{x}, {y}, {x + width}, {y + height}}}\nend tell'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return f"resized {app} window to {width}x{height}"
        except Exception as e:
            return f"resize failed: {e}"

    def _ask_chatgpt(self, args: dict) -> str:
        try:
            from llm import LLM
            question = args["question"]
            llm = LLM()
            messages = [
                {"role": "system", "content": "You are ChatGPT. Answer concisely."},
                {"role": "user", "content": question},
            ]
            return llm.generate_text(messages=messages).strip() or "(no response)"
        except Exception as e:
            return f"ask_chatgpt failed: {e}"

    def _time(self, args: dict) -> str:
        return _now()
