import json
import os
import platform
import random
import socket
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List

import brain.pi as pi


def _now() -> str:
    return datetime.now().strftime("%I:%M %p")


def _prop(typ: str, desc: str, **extra) -> dict:
    return {"type": typ, "description": desc, **extra}


def _schema(required: dict, optional: dict = None) -> dict:
    props = {**required, **(optional or {})}
    return {"type": "object", "properties": props, "required": list(required.keys())}


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
        self._register_all()

    # ── Declarative tool table ───────────────────────────────────────────

    def _register_all(self):
        specs = [
            dict(name="time", description="Get the current time.",
                 params=_schema({}), handler="_time"),
            dict(name="write_file",
                 description="Create or overwrite a file with the given content. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                 params=_schema({
                     "path": _prop("string", "File path, e.g. 'essay.txt' or 'code/hello.py'"),
                     "content": _prop("string", "Full content to write"),
                 }), handler="_write_file"),
            dict(name="read_file",
                 description="Read the contents of a file. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                 params=_schema({"path": _prop("string", "File path")}), handler="_read_file"),
            dict(name="edit_file",
                 description="Edit an EXISTING file by fully replacing its content. The file must already exist; if it doesn't, use write_file instead.",
                 params=_schema({
                     "path": _prop("string", "Path of an existing file to overwrite"),
                     "content": _prop("string", "New full content"),
                 }), handler="_edit_file", risky=True),
            dict(name="delete_file",
                 description="Permanently delete a file. Only use when the user explicitly asks.",
                 params=_schema({"path": _prop("string", "File path")}), handler="_delete_file", risky=True),
            dict(name="list_files",
                 description="List files in a directory. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                 params=_schema({"path": _prop("string", "Directory path")}), handler="_list_files"),
            dict(name="execute_command",
                 description="Run a shell command and return output. Only use when the user explicitly asks, and keep it safe.",
                 params=_schema({"command": _prop("string", "Shell command")}), handler="_execute_command", risky=True),
            dict(name="open_file",
                 description="Open a local file with the default application. Path is relative to ~ unless absolute.",
                 params=_schema({"path": _prop("string", "File path to open")}), handler="_open_file"),
            dict(name="open_website",
                 description="Open a URL in the default browser.",
                 params=_schema({"url": _prop("string", "Full URL to open")}), handler="_open_website"),
            dict(name="web_search",
                 description="Open a Bing web search in the default browser.",
                 params=_schema({"query": _prop("string", "Search query")}), handler="_web_search"),
            dict(name="read_website",
                 description="Fetch the text content of a webpage for research.",
                 params=_schema({"url": _prop("string", "URL to fetch")}), handler="_read_website"),
            dict(name="system_info",
                 description="Get basic system information (OS, version, architecture, hostname).",
                 params=_schema({}), handler="_system_info"),
            dict(name="open_app",
                 description="Open a macOS application by name.",
                 params=_schema({"name": _prop("string", "App name, e.g. 'Safari' or 'Visual Studio Code'")}), handler="_open_app"),
            dict(name="close_app",
                 description="Quit or force-close an application. Only use when the user explicitly asks.",
                 params=_schema({"app": _prop("string", "Application name")},
                                {"force": _prop("boolean", "Use killall instead of graceful quit")}),
                 handler="_close_app", risky=True),
            dict(name="toggle_wifi",
                 description="Toggle WiFi on or off. Only use when the user explicitly asks.",
                 params=_schema({}), handler="_toggle_wifi", risky=True),
            dict(name="toggle_airdrop",
                 description="Toggle AirDrop discoverability off/everyone. Only use when the user explicitly asks.",
                 params=_schema({}), handler="_toggle_airdrop", risky=True),
            dict(name="notify",
                 description="Show a macOS notification.",
                 params=_schema({
                     "title": _prop("string", "Notification title"),
                     "message": _prop("string", "Notification body"),
                 }), handler="_notify"),
            dict(name="type_text",
                 description="Type text into the currently focused application. Only use when the user explicitly asks.",
                 params=_schema({"text": _prop("string", "Text to type")}), handler="_type_text", risky=True),
            dict(name="press_key",
                 description="Press a key in the currently focused application (return, tab, escape, etc.). Only use when the user explicitly asks.",
                 params=_schema({"key": _prop("string", "Key name")}), handler="_press_key", risky=True),
            dict(name="get_volume",
                 description="Get the current system output volume percentage.",
                 params=_schema({}), handler="_get_volume"),
            dict(name="set_volume",
                 description="Set the system output volume (0-100). Only use when the user explicitly asks.",
                 params=_schema({"level": _prop("integer", "Volume level 0-100")}), handler="_set_volume", risky=True),
            dict(name="move_mouse",
                 description="Move the mouse cursor by (dx, dy) pixels. Only use when the user explicitly asks.",
                 params=_schema({
                     "dx": _prop("integer", "Horizontal delta"),
                     "dy": _prop("integer", "Vertical delta"),
                 }), handler="_move_mouse", risky=True),
            dict(name="shake_mouse",
                 description="Jitter the mouse cursor around for a moment. Only use when the user explicitly asks.",
                 params=_schema({}), handler="_shake_mouse", risky=True),
            dict(name="click_mouse",
                 description="Click the mouse at optional (x, y) coordinates. Only use when the user explicitly asks.",
                 params=_schema({}, {
                     "x": _prop("integer", "X coordinate"),
                     "y": _prop("integer", "Y coordinate"),
                     "clicks": _prop("integer", "Number of clicks"),
                 }), handler="_click_mouse", risky=True),
            dict(name="get_mouse_position",
                 description="Get the current mouse cursor coordinates.",
                 params=_schema({}), handler="_get_mouse_position"),
            dict(name="get_clipboard",
                 description="Read the current clipboard text.",
                 params=_schema({}), handler="_get_clipboard"),
            dict(name="set_clipboard",
                 description="Set the clipboard text.",
                 params=_schema({"text": _prop("string", "Text to copy")}), handler="_set_clipboard"),
            dict(name="close_front_window",
                 description="Close the frontmost window (Command+W). Useful for playful pranks. Will not close the terminal Mira is running in.",
                 params=_schema({}), handler="_close_front_window", risky=True),
            dict(name="minimize_front_window",
                 description="Minimize the frontmost window. Will not minimize Mira's terminal.",
                 params=_schema({}), handler="_minimize_front_window", risky=True),
            dict(name="resize_window",
                 description="Resize/reposition the front window of an application. Only use when the user explicitly asks.",
                 params=_schema({"app": _prop("string", "Application name")}, {
                     "width": _prop("integer", "Window width"),
                     "height": _prop("integer", "Window height"),
                     "x": _prop("integer", "X position"),
                     "y": _prop("integer", "Y position"),
                 }), handler="_resize_window", risky=True),
            dict(name="ask_chatgpt",
                 description="Ask ChatGPT a question and get a response. Use this for research or when you need another opinion.",
                 params=_schema({"question": _prop("string", "Full question to ask")}), handler="_ask_chatgpt"),
            dict(name="say",
                 description="Speak text out loud using macOS text-to-speech.",
                 params=_schema({"text": _prop("string", "Text to speak")}), handler="_say"),
            dict(name="screenshot",
                 description="Capture the screen to a PNG file. Path is relative to ~/Desktop/MiraFiles/ unless absolute.",
                 params=_schema({}, {"path": _prop("string", "Output path, e.g. 'shot.png'")}), handler="_screenshot"),
            dict(name="search_web",
                 description="Perform a real web search and return the top result summaries (titles, URLs, snippets). Use this instead of web_search when you need actual answers.",
                 params=_schema({"query": _prop("string", "Search query")},
                                {"num_results": _prop("integer", "How many results (1-8)")}),
                 handler="_search_web"),
            dict(name="search_files",
                 description="Grep-style search for a text pattern in files under a directory. Great for finding code or text in your projects.",
                 params=_schema({"query": _prop("string", "Text pattern to search for (regex ok)")}, {
                     "directory": _prop("string", "Directory to search (default: current working dir)"),
                     "file_filter": _prop("string", "Only search files matching this glob, e.g. '*.py'"),
                 }), handler="_search_files"),
            dict(name="search_chat_history",
                 description="Search Mira's own past chat conversations for a message, topic, or pattern. Use this when you need to recall earlier chats.",
                 params=_schema({"query": _prop("string", "Text/pattern to find in past chats")}, {
                     "max_results": _prop("integer", "Max matches to return"),
                 }), handler="_search_chat_history"),
            dict(name="search_hermes_memory",
                 description="Search the pi coding agent's (hermes') durable memory files for a topic. Lets Mira consult the same long-term memory the coding agent keeps.",
                 params=_schema({"query": _prop("string", "Text/pattern to find in hermes memory")}, {
                     "max_results": _prop("integer", "Max matches to return"),
                 }), handler="_search_hermes_memory"),
        ]

        for spec in specs:
            self.register(Tool(
                name=spec["name"],
                description=spec["description"],
                parameters=spec["params"],
                execute=getattr(self, spec["handler"]),
                risky=spec.get("risky", False),
            ))

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

    # ── Shared helpers ───────────────────────────────────────────────────

    def _osascript(self, script: str) -> str:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def _frontmost_app(self) -> str:
        try:
            return self._osascript(
                'tell application "System Events" to get name of first application process whose frontmost is true'
            ).lower()
        except Exception:
            return ""

    def _resolve_path(self, path_str: str) -> Path:
        path = Path(os.path.expanduser(path_str))
        if not path.is_absolute():
            path = Path.home() / "Desktop" / "MiraFiles" / path
        return path

    def _is_safe(self, path: Path) -> bool:
        try:
            return str(path.resolve()).startswith(str(Path.home().resolve()))
        except Exception:
            return False

    def _atomic_write(self, path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)

    # ── File tools ───────────────────────────────────────────────────────

    def _write_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path} outside allowed directory"
        self._atomic_write(path, args["content"])
        return f"saved {path}"

    def _edit_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path} outside allowed directory"
        if not path.exists():
            return f"error: {path} does not exist. Use write_file to create it."
        self._atomic_write(path, args["content"])
        return f"edited {path}"

    def _read_file(self, args: dict) -> str:
        path = self._resolve_path(args["path"])
        if not self._is_safe(path):
            return f"refused: {path}"
        if path.suffix.lower() == ".pdf":
            return self._read_pdf(path)
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            return f"error reading {path}: {e}"

    def _read_pdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            parts = [page.extract_text() for page in reader.pages if page.extract_text()]
            text = "\n".join(parts).strip()
            if not text:
                return "pdf exists but has no extractable text"
            if len(text) > 8000:
                text = text[:8000] + "\n\n[truncated: pdf has more text]"
            return text
        except ImportError:
            return "pdf found but pypdf is not installed. run: pip3 install pypdf"
        except Exception as e:
            return f"could not read pdf: {e}"

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
            return "\n".join(e.name for e in entries) or "(empty)"
        except Exception as e:
            return f"error listing {dir_path}: {e}"

    def _execute_command(self, args: dict) -> str:
        try:
            result = subprocess.run(
                args["command"], shell=True, capture_output=True, text=True, timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            return output[:2000] or "(no output)"
        except Exception as e:
            return f"command failed: {e}"

    # ── Web / system tools ───────────────────────────────────────────────

    def _open_file(self, args: dict) -> str:
        path = Path(os.path.expanduser(args["path"])).resolve()
        if not str(path).startswith(str(Path.home().resolve())):
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
        url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
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
            text = " ".join(" ".join(parser.texts).split())
            if not text.strip():
                return "page loaded but no readable text found"
            return text[:4000] + "..." if len(text) > 4000 else text
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

    # ── macOS app / window tools ─────────────────────────────────────────

    def _open_app(self, args: dict) -> str:
        try:
            subprocess.run(["open", "-a", args["name"]], check=True)
            return f"opened {args['name']}"
        except Exception as e:
            return f"error opening app: {e}"

    def _close_app(self, args: dict) -> str:
        app = args.get("app", "")
        if not app:
            return "no app name given"
        try:
            if str(args.get("force", "false")).lower() in ("true", "1", "yes"):
                subprocess.run(["killall", app], check=False, capture_output=True, text=True)
                return f"force-closed {app}"
            self._osascript(f'quit application "{app}"')
            return f"closed {app}"
        except Exception as e:
            return f"close failed: {e}"

    def _toggle_wifi(self, args: dict) -> str:
        try:
            for interface in ["en0", "en1", "Wi-Fi", "AirPort"]:
                try:
                    result = subprocess.run(
                        ["networksetup", "-getairportpower", interface],
                        capture_output=True, text=True, check=False,
                    )
                    if "Error" not in result.stdout and "You cannot" not in result.stdout:
                        new_state = "off" if "on" in result.stdout.lower() else "on"
                        subprocess.run(
                            ["networksetup", "-setairportpower", interface, new_state],
                            capture_output=True, text=True, check=False,
                        )
                        return f"wifi toggled to {new_state} ({interface})"
                except Exception:
                    continue
            return "wifi toggle failed: could not find interface"
        except Exception as e:
            return f"wifi toggle failed: {e}"

    def _toggle_airdrop(self, args: dict) -> str:
        try:
            result = subprocess.run(
                ["defaults", "read", "com.apple.sharingd", "DiscoverableMode"],
                capture_output=True, text=True, check=False,
            )
            try:
                current = int(result.stdout.strip())
            except Exception:
                current = 0
            new_mode = 0 if current > 0 else 2
            subprocess.run(
                ["defaults", "write", "com.apple.sharingd", "DiscoverableMode", "-int", str(new_mode)],
                check=True, capture_output=True, text=True,
            )
            return f"airdrop toggled to {'off' if new_mode == 0 else 'everyone'}"
        except Exception as e:
            return f"airdrop toggle failed: {e}"

    def _notify(self, args: dict) -> str:
        title = args["title"].replace('"', '\\"')
        message = args["message"].replace('"', '\\"')
        try:
            self._osascript(f'display notification "{message}" with title "{title}"')
            return "notification sent"
        except Exception as e:
            return f"notification failed: {e}"

    def _close_front_window(self, args: dict) -> str:
        try:
            front_app = self._frontmost_app()
            if front_app in ("terminal", "iterm", "ghostty"):
                return "refused: front window is the terminal"
            self._osascript('tell application "System Events" to keystroke "w" using command down')
            return f"closed front window of {front_app}"
        except Exception as e:
            return f"close front window failed: {e}"

    def _minimize_front_window(self, args: dict) -> str:
        try:
            front_app = self._frontmost_app()
            if front_app in ("terminal", "iterm", "ghostty"):
                return "refused: front window is the terminal"
            self._osascript('tell application "System Events" to keystroke "m" using command down')
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
            self._osascript(
                f'tell application "{app}"\n'
                f"    set bounds of front window to {{{x}, {y}, {x + width}, {y + height}}}\n"
                "end tell"
            )
            return f"resized {app} window to {width}x{height}"
        except Exception as e:
            return f"resize failed: {e}"

    # ── Input / clipboard / volume tools ─────────────────────────────────

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
            self._osascript(f'tell application "System Events" to keystroke "{text.replace(chr(34), chr(92) + chr(34))}"')
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
                "return": "key code 36", "enter": "key code 36", "tab": "key code 48",
                "escape": "key code 53", "esc": "key code 53", "space": "key code 49",
                "backspace": "key code 51", "delete": "key code 51",
                "up": "key code 126", "down": "key code 125",
                "left": "key code 123", "right": "key code 124",
            }
            action = key_map.get(key)
            if not action:
                return f"unknown key: {key}"
            self._osascript(f'tell application "System Events" to {action}')
            return f"pressed {key}"
        except Exception as e:
            return f"key press failed: {e}"

    def _get_volume(self, args: dict) -> str:
        try:
            volume = self._osascript("output volume of (get volume settings)")
            return f"volume is {volume}%"
        except Exception as e:
            return f"volume check failed: {e}"

    def _set_volume(self, args: dict) -> str:
        try:
            level = max(0, min(100, int(args.get("level", 50))))
            self._osascript(f"set volume output volume {level}")
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
            screen_w, screen_h = pyautogui.size()
            for _ in range(40):
                pyautogui.moveTo(random.randint(0, screen_w), random.randint(0, screen_h), duration=0.05)
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
            subprocess.run(["pbcopy"], input=args.get("text", ""), text=True, check=True)
            return "clipboard set"
        except Exception as e:
            return f"clipboard set failed: {e}"

    # ── AI / voice / capture tools ───────────────────────────────────────

    def _ask_chatgpt(self, args: dict) -> str:
        try:
            from brain.llm import LLM
            llm = LLM()
            messages = [
                {"role": "system", "content": "You are ChatGPT. Answer concisely."},
                {"role": "user", "content": args["question"]},
            ]
            return llm.generate_text(messages=messages).strip() or "(no response)"
        except Exception as e:
            return f"ask_chatgpt failed: {e}"

    def _say(self, args: dict) -> str:
        text = args["text"]
        try:
            subprocess.run(["say", text], check=True)
            return f"said: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"say failed: {e}"

    def _screenshot(self, args: dict) -> str:
        path = self._resolve_path(args.get("path", "screenshot.png"))
        if not self._is_safe(path):
            return f"refused: {path} outside allowed directory"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(["screencapture", "-x", str(path)], capture_output=True, text=True)
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                if "could not create image" in err or "not allowed" in err:
                    return ("screenshot failed: terminal needs Screen Recording permission. "
                            "Enable it in System Settings > Privacy & Security > Screen Recording, then restart the terminal.")
                return f"screenshot failed: {err or 'unknown error'}"
            return f"screenshot saved to {path}"
        except Exception as e:
            return f"screenshot failed: {e}"

    def _time(self, args: dict) -> str:
        return _now()

    # ── Pi integration tools ──────────────────────────────────────────────

    def _search_web(self, args: dict) -> str:
        query = args.get("query", "")
        if not query:
            return "no query given"
        try:
            return pi.web_search(query, int(args.get("num_results", 5)))
        except Exception as e:
            return f"web search failed: {e}"

    def _search_files(self, args: dict) -> str:
        return pi.search_files(
            args.get("query", ""),
            directory=args.get("directory", ""),
            file_filter=args.get("file_filter", ""),
        )

    def _search_chat_history(self, args: dict) -> str:
        return pi.search_chat_history(
            args.get("query", ""),
            max_results=int(args.get("max_results", 10)),
        )

    def _search_hermes_memory(self, args: dict) -> str:
        return pi.search_hermes_memory(
            args.get("query", ""),
            max_results=int(args.get("max_results", 10)),
        )
