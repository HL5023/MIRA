import os
import random
import subprocess
import sys
import threading
import time
from datetime import datetime
from queue import Queue, Empty

try:
    import curses
except ImportError:
    curses = None

from brain.core import MiraCore
from brain.llm import load_env_file, get_api_key


# Hide the Python icon from the macOS Dock while Mira runs.
if sys.platform == "darwin":
    try:
        from AppKit import NSApplication

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory
    except Exception:
        pass


class MiraRenderer:
    """Modern, pi-style terminal chat UI.

    Layout:
        ┌ mood-tinted app bar (MIRA // INDEV | Mood · time) ┐
        │ chat messages (YOU / MIRA / TOOLS labels)          │
        │ dim status line (mood · patience | help hints)      │
        └ solid green input bar ( > prompt)                   ┘
    """

    def __init__(self, stdscr, personality):
        self.stdscr = stdscr
        self.personality = personality
        self.screen_height = 0
        self.screen_width = 0

        # UI sub-windows
        self.header_win = None
        self.chat_win = None
        self.status_win = None
        self.input_win = None

        # Animation state
        self.typing_frame = 0
        self.last_render = time.time()
        self.scroll_offset = 0

        self._init_colors()
        try:
            curses.curs_set(0)
        except Exception:
            pass

    def _init_colors(self):
        if not curses.has_colors():
            self.mood_color_pairs = {}
            self.bar_pairs = {}
            return
        curses.start_color()
        curses.use_default_colors()

        # Base pairs
        curses.init_pair(1, curses.COLOR_WHITE, -1)   # default text
        curses.init_pair(2, curses.COLOR_GREEN, -1)   # user / prompt
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # tools
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # status accents
        curses.init_pair(5, curses.COLOR_CYAN, -1)    # mira label default
        curses.init_pair(7, curses.COLOR_BLUE, -1)    # sad

        # Solid background bars (modern app-bar look; no box-drawing chars)
        # index -> (front_color, back_color). Header bar tinted per-mood.
        curses.init_pair(30, curses.COLOR_BLACK, curses.COLOR_CYAN)    # calm bar
        curses.init_pair(31, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # happy bar
        curses.init_pair(32, curses.COLOR_WHITE, curses.COLOR_BLUE)    # sad bar
        curses.init_pair(33, curses.COLOR_WHITE, curses.COLOR_RED)     # angry bar
        curses.init_pair(34, curses.COLOR_BLACK, curses.COLOR_GREEN)   # excited bar
        curses.init_pair(35, curses.COLOR_BLACK, curses.COLOR_WHITE)   # tired bar
        curses.init_pair(36, curses.COLOR_BLACK, curses.COLOR_MAGENTA) # bored bar
        curses.init_pair(37, curses.COLOR_BLACK, curses.COLOR_CYAN)    # curious bar
        curses.init_pair(40, curses.COLOR_BLACK, curses.COLOR_GREEN)   # input bar

        self.bar_pairs = {
            "calm": 30, "happy": 31, "sad": 32, "angry": 33,
            "excited": 34, "tired": 35, "bored": 36, "curious": 37,
        }
        self.input_bar_pair = 40
        self.mood_color_pairs = self._init_mood_colors()
        self.dim = curses.A_DIM

    def _init_mood_colors(self):
        """Text color pairs per mood for chat labels/body."""
        mood_styles = [
            ("calm", curses.COLOR_WHITE, 0, -1),
            ("happy", curses.COLOR_YELLOW, curses.A_BOLD, -1),
            ("sad", curses.COLOR_BLUE, 0, -1),
            ("angry", curses.COLOR_RED, curses.A_BOLD, -1),
            ("excited", curses.COLOR_GREEN, curses.A_BOLD, -1),
            ("tired", curses.COLOR_WHITE, curses.A_DIM, -1),
            ("bored", curses.COLOR_MAGENTA, 0, -1),
            ("curious", curses.COLOR_CYAN, curses.A_BOLD, -1),
        ]
        pairs = {}
        for idx, (mood, fg, attr, bg) in enumerate(mood_styles, start=10):
            pair_num = idx
            curses.init_pair(pair_num, fg, bg)
            pairs[mood] = curses.color_pair(pair_num) | attr
        return pairs

    def _drain_paste_input(self, stdscr):
        """Read any immediately-pending characters and turn pasted newlines into spaces."""
        stdscr.timeout(0)
        extra = ""
        deadline = time.time() + 0.05
        while time.time() < deadline:
            try:
                ch = stdscr.getch()
            except curses.error:
                ch = -1
            if ch == -1:
                continue
            if ch in (10, 13):
                extra += " "
            elif 32 <= ch < 127:
                extra += chr(ch)
            elif ch in (127, curses.KEY_BACKSPACE, 263):
                if extra:
                    extra = extra[:-1]
                else:
                    break
            else:
                break
            deadline = time.time() + 0.05
        stdscr.timeout(100)
        return extra

    def resize(self):
        self.screen_height, self.screen_width = self.stdscr.getmaxyx()
        if self.screen_height < 7 or self.screen_width < 24:
            return

        # Header app bar (1 row, no thick separator), chat middle, status + input bottom.
        self.header_win = curses.newwin(1, self.screen_width, 0, 0)
        self.status_win = curses.newwin(1, self.screen_width, self.screen_height - 2, 0)
        self.input_win = curses.newwin(1, self.screen_width, self.screen_height - 1, 0)

        chat_y = 1
        chat_height = max(1, self.screen_height - chat_y - 2)
        self.chat_win = curses.newwin(chat_height, self.screen_width, chat_y, 0)

        self.stdscr.clear()

    def _display_width(self, s: str) -> int:
        width = 0
        for ch in s:
            if ord(ch) > 127:
                width += 2
            else:
                width += 1
        return width

    def _fill_bar(self, win, pair: int, attr=0):
        """Fill the whole window width with a solid colored bar (background)."""
        h, w = win.getmaxyx()
        bar_attr = curses.color_pair(pair) | attr
        try:
            win.addnstr(0, 0, " " * max(1, w), w, bar_attr)
        except curses.error:
            pass
        return bar_attr

    def _draw_header(self, state=None):
        """Mood-tinted full-width app bar: version left, mood + duration right."""
        if not self.header_win:
            return
        self.header_win.clear()
        mood = "calm"
        session_start = time.time()
        if state:
            mood = state.get("mood", "calm")
            session_start = state.get("session_start", time.time())
        duration = self._format_duration(session_start)
        w = self.screen_width

        bar_pair = self.bar_pairs.get(mood, 30)
        bar_attr = self._fill_bar(self.header_win, bar_pair, curses.A_BOLD)

        left = " MIRA // INDEV 26.3.1"
        right = f" {mood.upper()} · {duration}"
        try:
            self.header_win.addnstr(0, 0, left, w, bar_attr)
            if len(right) < w:
                self.header_win.addnstr(0, w - len(right), right, len(right), bar_attr)
        except curses.error:
            pass
        self.header_win.refresh()

    def _wrap(self, text, width):
        if width < 1:
            return [""]
        lines = []
        while text:
            if len(text) <= width:
                lines.append(text)
                break
            break_at = text.rfind(" ", 0, width + 1)
            if break_at <= 0:
                break_at = width
            lines.append(text[:break_at])
            text = text[break_at:].lstrip()
        return lines if lines else [""]

    def _draw_chat(self, chat_lines, mood="normal"):
        if not self.chat_win:
            return
        self.chat_win.clear()
        h, w = self.chat_win.getmaxyx()
        if h <= 0:
            self.chat_win.refresh()
            return

        units = []
        for item in chat_lines:
            if isinstance(item, tuple) and len(item) >= 4:
                sender, text, msg_time, msg_mood = item[0], item[1], item[2], item[3]
            elif isinstance(item, tuple) and len(item) >= 3:
                sender, text, msg_mood = item[0], item[1], item[2]
                msg_time = datetime.now().strftime("%H:%M")
            else:
                sender, text = item
                msg_time = datetime.now().strftime("%H:%M")
                msg_mood = None

            if sender == self.personality.name:
                color = self.mood_color_pairs.get(msg_mood, curses.color_pair(5))
                sender_name = "MIRA"
                label_attr = color | curses.A_BOLD
                text_attr = color
            elif sender == "You":
                sender_name = "YOU"
                label_attr = curses.color_pair(2) | curses.A_BOLD
                text_attr = curses.color_pair(2)
            elif sender is None:
                sender_name = "TOOLS"
                label_attr = self.dim
                text_attr = curses.color_pair(3) | self.dim
            else:
                sender_name = sender.upper()
                label_attr = curses.color_pair(3) | curses.A_BOLD
                text_attr = curses.color_pair(3)

            # Inline style: sender · time, message follows on the same line,
            # wrapped continuation lines align under the message text.
            prefix = f"{sender_name:<6}· {msg_time}"       # e.g. "YOU   · 20:48"
            msg_indent = len(prefix) + 2                    # align wraps under the text
            body_width = max(1, w - 1 - msg_indent)

            wrapped = self._wrap(text, body_width)
            unit = []
            for i, sub in enumerate(wrapped):
                if i == 0:
                    line = f"{prefix}  {sub}"
                    unit.append((line, label_attr, 0))
                else:
                    unit.append((sub, text_attr, msg_indent))
            unit.append(("", self.dim, 0))  # blank spacer between messages
            units.append(unit)

        # Clamp scroll offset
        total_units = len(units)
        max_visible = 0
        rem = h
        for unit in reversed(units):
            if len(unit) > rem:
                break
            rem -= len(unit)
            max_visible += 1
        max_offset = max(0, total_units - max_visible)
        self.scroll_offset = max(0, min(self.scroll_offset, max_offset))

        candidates = units[:-self.scroll_offset] if self.scroll_offset else units[:]
        visible = []
        remaining = h
        for unit in reversed(candidates):
            if len(unit) > remaining:
                break
            visible.insert(0, unit)
            remaining -= len(unit)

        y = 0
        for unit in visible:
            for text, color, indent in unit:
                if y >= h:
                    break
                text = text[:w - 1 - indent]
                try:
                    self.chat_win.addnstr(y, indent, text, w - 1 - indent, color)
                except curses.error:
                    pass
                y += 1

        self.chat_win.refresh()

    def draw_input(self, buffer):
        """Solid green input bar: '> prompt text' — no fragile box chars."""
        if not self.input_win:
            return
        self.input_win.clear()
        w = self.screen_width
        bar_attr = self._fill_bar(self.input_win, self.input_bar_pair, curses.A_BOLD)

        prompt = "> "
        max_visible = max(1, w - len(prompt) - 2)
        if len(buffer) > max_visible:
            visible = "…" + buffer[-(max_visible - 1):]
        else:
            visible = buffer
        line = prompt + visible
        try:
            self.input_win.addnstr(0, 0, line[:w - 1], w - 1, bar_attr)
        except curses.error:
            pass
        self.input_win.refresh()

    def draw_status(self, state=None):
        """Dim status line above the input: mood · patience | hints."""
        if not self.status_win:
            return
        self.status_win.clear()
        w = self.screen_width - 1
        mood = "calm"
        patience = 1.0
        if state:
            mood = state.get("mood", "calm")
            patience = state.get("patience", 1.0)
        status = f"  {mood.upper()} · PATIENCE {patience:.0%}"
        right = "ESC TO QUIT · /HELP"
        try:
            self.status_win.addnstr(0, 0, status, w, self.dim)
            if len(right) < w:
                self.status_win.addnstr(0, w - len(right), right, len(right), self.dim)
        except curses.error:
            pass
        self.status_win.refresh()

    def _format_duration(self, session_start):
        elapsed = time.time() - (session_start or time.time())
        hours, rem = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}h{minutes:02d}m"
        return f"{minutes:02d}:{seconds:02d}"

    def render(self, state):
        now = time.time()
        self.last_render = now

        mode = state.get("mode", "idle")
        if mode in ("typing", "thinking"):
            self.typing_frame += 1

        typing = ""
        if mode in ("typing", "thinking"):
            spin = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            typing = spin[self.typing_frame % len(spin)] + " MIRA IS TYPING…"

        self._draw_header(state)
        self._draw_chat(state.get("chat_lines", []), state.get("mood", "normal"))
        self.draw_status(state)
        self._draw_typing(typing)

    def _draw_typing(self, text):
        if not text or not self.chat_win:
            return
        h, w = self.chat_win.getmaxyx()
        if h <= 0:
            return
        try:
            self.chat_win.addnstr(h - 1, 2, text, w - 3, self.dim)
            self.chat_win.refresh()
        except curses.error:
            pass

    def cleanup(self):
        try:
            curses.curs_set(1)
        except Exception:
            pass



class MiraTerminal:
    """Curses terminal UI that drives a MiraCore instance."""

    def __init__(self, core: MiraCore):
        self.core = core
        self.stdscr = None
        self.ui = None
        self.chat_lines = []
        self.input_buffer = ""
        self.typing_state = None
        self.session_start = time.time()
        self.running = True

        self.user_queue = Queue()
        self.mira_queue = Queue()
        self.mira_thread = None
        self.idle_thread = None

        # Burst / spam detection
        self.last_user_input_time = 0.0
        self.burst_count = 0
        self.burst_window = 1.5
        self.burst_threshold = 3

        # Repeat detection
        self.last_user_text = ""
        self.last_user_time = 0.0
        self.repeat_count = 0
        self.repeat_window = 3.0

        # Batch messaging
        self.pending_batch = []
        self.pending_tag_context = []
        self.batch_deadline = None
        self.batch_delay = 0.0

    # ── UI helpers ───────────────────────────────────────────────────────────

    def _chat_message(self, sender, text):
        if not text:
            return
        collapsed = " ".join(str(text).split()).strip()
        if not collapsed:
            return
        timestamp = datetime.now().strftime("%H:%M")
        if sender == self.core.personality.name:
            self.chat_lines.append((sender, collapsed, timestamp, self.core.personality.mood))
            self.core.last_replies.append(collapsed)
            self.core.last_replies = self.core.last_replies[-5:]
        else:
            self.chat_lines.append((sender, collapsed, timestamp, None))

    def _ui_state(self):
        mode = self.typing_state if self.typing_state else "idle"
        return {
            "mood": self.core.personality.mood,
            "patience": self.core.personality.patience,
            "chat_lines": self.chat_lines,
            "input_buffer": self.input_buffer,
            "mode": mode,
            "session_start": self.session_start,
        }

    # ── Async workers ───────────────────────────────────────────────────────

    def _mira_worker(self):
        while self.running:
            try:
                item = self.user_queue.get(timeout=0.5)
            except Empty:
                continue
            self.last_user_time = time.time()

            if isinstance(item, tuple):
                user_input, tag_context = item
            else:
                user_input, tag_context = item, ""

            self.typing_state = "typing"
            time.sleep(random.uniform(0.05, 0.1))

            self.typing_state = "thinking"
            try:
                for sender, text, is_delta in self.core.respond_stream(user_input, context=tag_context or None):
                    self.mira_queue.put((sender, text, is_delta))
            except RuntimeError as e:
                # Mira ran out of patience — this is a real shutdown, not an AI failure.
                if self.core.exit_message:
                    self.mira_queue.put((self.core.personality.name, self.core.exit_message))
                else:
                    self.mira_queue.put((self.core.personality.name, f"(AI failed: {e})"))
                self.mira_queue.put((None, "_close_terminal"))
                self.typing_state = None
                continue
            except Exception as e:
                self.mira_queue.put((self.core.personality.name, f"(AI failed: {e})"))
                self.typing_state = None
                continue

            self.typing_state = None

    def _idle_worker(self):
        while self.running:
            time.sleep(10)
            if not self.running:
                return
            if not self.user_queue.empty() or self.typing_state:
                self.last_user_time = time.time()
                continue

            self.core.personality.comfort(0.01)
            self.core.personality.maybe_drift()

            # Auto-prank when happy, every 15-20 minutes.
            if self.core.personality.mood == "happy" and self.core.next_prank_time and time.time() >= self.core.next_prank_time:
                try:
                    result = self.core._do_prank()
                    self.mira_queue.put((None, f"pranked: {result}"))
                    self.core._notify_user("Mira", f"pranked: {result}")
                except Exception:
                    pass
                self.core._schedule_next_prank()
                self.last_user_time = time.time()
                continue

            elapsed = time.time() - self.last_user_time
            if int(elapsed) % 60 != 0:
                continue

            if elapsed > 600 and random.random() < 0.05:
                self.core.personality.annoy(0.03, "ghosted")

            if self.core.personality.mood == "angry" and elapsed > 600:
                if random.random() < 0.3:
                    self._say_idle(
                        "Mira is angry and has been ignored. Generate a short 'im leaving' message in her voice. Start with [emotion].",
                        "im done waiting",
                    )
                    self.mira_queue.put((None, "_close_terminal"))
                    return

            if self.core.personality.mood == "angry" and elapsed > 180:
                if random.random() < 0.1:
                    self._say_idle(
                        "Mira is angry and feels ignored. Generate a short, snappy message. Start with [emotion].",
                        "wow ok ignore me",
                    )
            elif elapsed > 1200:
                if random.random() < 0.1:
                    self._say_idle(
                        "Mira has been left alone. Generate a short message checking if the user is still there. Start with [emotion].",
                        "u still there?",
                    )

    def _say_idle(self, prompt: str, fallback: str):
        """Generate a short idle message from Mira and queue it for display."""
        try:
            reply = self.core.llm.generate_text(prompt=prompt)
            reply = self.core.llm.clean_reply(reply) if reply else fallback
        except Exception:
            reply = fallback
        self.mira_queue.put((self.core.personality.name, reply))

    def _flush_batch(self):
        if not self.pending_batch:
            self.batch_deadline = None
            return
        combined = " ".join(self.pending_batch)
        tag_context = "\n".join(self.pending_tag_context)
        self.pending_batch = []
        self.pending_tag_context = []
        self.batch_deadline = None
        self.user_queue.put((combined, tag_context))

    def _close_terminal(self):
        self.core._close_terminal()
        self.running = False

    def _reset_terminal(self):
        try:
            sys.stdout.write("\033c")
            sys.stdout.flush()
        except Exception:
            pass
        try:
            subprocess.run(["stty", "sane"], check=False, capture_output=True, text=True)
        except Exception:
            pass
        try:
            subprocess.run(["tput", "reset"], check=False, capture_output=True, text=True)
        except Exception:
            pass

    # ── Run ─────────────────────────────────────────────────────────────────

    def _run_curses(self, stdscr):
        self.stdscr = stdscr
        self.stdscr.keypad(True)
        self.stdscr.timeout(100)

        self.ui = MiraRenderer(stdscr, self.core.personality)
        self.ui.resize()

        self.mira_thread = threading.Thread(target=self._mira_worker, daemon=True)
        self.idle_thread = threading.Thread(target=self._idle_worker, daemon=True)
        self.mira_thread.start()
        self.idle_thread.start()

        self.last_user_time = time.time()

        while self.running:
            try:
                self.ui.render(self._ui_state())

                while not self.mira_queue.empty():
                    item = self.mira_queue.get_nowait()
                    sender, text = item[0], item[1]
                    is_delta = item[2] if len(item) > 2 else False
                    if text == "_close_terminal":
                        self._close_terminal()
                        break
                    if is_delta and self.chat_lines and self.chat_lines[-1][0] == sender:
                        # update last message
                        old = self.chat_lines[-1]
                        self.chat_lines[-1] = (old[0], old[1] + text, *old[2:])
                    else:
                        self._chat_message(sender, text)

                if not self.running:
                    break

                try:
                    ch = self.stdscr.getch()
                except curses.error:
                    ch = -1

                if self.batch_deadline and time.time() >= self.batch_deadline:
                    self._flush_batch()

                if ch == -1:
                    continue

                if ch == curses.KEY_UP:
                    self.ui.scroll_offset += 1
                    continue
                if ch == curses.KEY_DOWN:
                    self.ui.scroll_offset = max(0, self.ui.scroll_offset - 1)
                    continue
                if ch == curses.KEY_PPAGE:
                    self.ui.scroll_offset += 5
                    continue
                if ch == curses.KEY_NPAGE:
                    self.ui.scroll_offset = max(0, self.ui.scroll_offset - 5)
                    continue
                if ch in (curses.KEY_LEFT, curses.KEY_RIGHT,
                          curses.KEY_HOME, curses.KEY_END,
                          curses.KEY_MOUSE):
                    continue

                if ch == curses.KEY_RESIZE:
                    self.ui.resize()
                    continue

                try:
                    if ch in (10, 13):
                        extra = self.ui._drain_paste_input(self.stdscr)
                        if extra:
                            self.input_buffer += extra
                            self.ui.draw_input(self.input_buffer)

                        if self.input_buffer.strip():
                            user_input = self.input_buffer.strip()
                            self.input_buffer = ""
                            self.ui.draw_input("")

                            # Burst detection
                            now = time.time()
                            if now - self.last_user_input_time <= self.burst_window:
                                self.burst_count += 1
                            else:
                                self.burst_count = 1
                            self.last_user_input_time = now

                            if self.burst_count >= self.burst_threshold:
                                if self.core.spam_filter_enabled:
                                    self._chat_message(None, "spam burst detected, ignored")
                                    continue
                                if self.burst_count == self.burst_threshold:
                                    self._chat_message(self.core.personality.name, self.core._say(
                                        "The user just spammed you with messages. Reply annoyed, tell them to chill out. One short line.",
                                        "bruh stop spamming"))
                                continue

                            # Detect accidental keyboard/code spam
                            if self.core.spam_filter_enabled and self.core._is_spam_input(user_input):
                                self._chat_message(None, "keyboard spam detected, ignored")
                                continue

                            clean_input, tag_context = self.core._extract_narrator_tags(user_input)
                            clean_input = self.core._normalize_input(clean_input)

                            now = time.time()
                            if clean_input == self.last_user_text and now - self.last_user_time < self.repeat_window:
                                self.repeat_count += 1
                                if self.repeat_count == 3:
                                    self.mira_queue.put((self.core.personality.name, self.core._say(
                                        "The user keeps repeating the same word. Reply annoyed, tell them to stop repeating. One short line.",
                                        "stop repeating.")))
                                continue
                            else:
                                self.repeat_count = 0
                                self.last_user_text = clean_input
                                self.last_user_time = now

                            # Slash commands are routed through core.respond
                            if clean_input.startswith("/"):
                                messages = self.core.respond(clean_input)
                                for sender, text in messages:
                                    self._chat_message(sender, text)
                                continue

                            self._chat_message("You", clean_input)
                            self.pending_batch.append(clean_input)
                            if tag_context:
                                self.pending_tag_context.append(tag_context)
                            if self.batch_delay <= 0:
                                self._flush_batch()
                            else:
                                self.batch_deadline = time.time() + self.batch_delay

                    elif ch in (127, curses.KEY_BACKSPACE, 263):
                        self.input_buffer = self.input_buffer[:-1]
                        self.ui.draw_input(self.input_buffer)

                    elif ch == 27:
                        self.running = False
                        break

                    elif 32 <= ch < 127:
                        self.input_buffer += chr(ch)
                        self.ui.draw_input(self.input_buffer)

                except (curses.error, ValueError):
                    continue

            except KeyboardInterrupt:
                self._chat_message(self.core.personality.name, self.core._say(
                    "The user interrupted you with Ctrl+C. Reply in-character, slightly offended that they cut you off. One short line.",
                    "rude :/"))
                self.running = False
                break
            except curses.error:
                break

        self.running = False
        if self.ui:
            self.ui.cleanup()

    def run_simple(self):
        while self.running:
            try:
                state = self.core.personality.state()
                print(f"\n{state['mood']} | patience {state['patience']:.0%} | {datetime.now().strftime('%H:%M')}")
                user_input = input("you > ").strip()
                if not user_input:
                    continue


                time.sleep(random.uniform(3, 8))
                messages = self.core.respond(user_input)
                for sender, text in messages:
                    print(f"[{sender}] {text}")

            except KeyboardInterrupt:
                print(f"\n[{self.core.personality.name}] rude :/")
                break

    def run(self, core=None):
        if core is not None:
            self.core = core
        if curses is None:
            print("curses not available, falling back to simple mode")
            self.run_simple()
            return
        try:
            curses.wrapper(self._run_curses)
        except Exception:
            self._reset_terminal()
            raise
        else:
            self._reset_terminal()
        if self.core.exit_message:
            print("\n*creates traceback*")
            print("Traceback (most recent call last):")
            print('  File "main.py", line 1, in <module>')
            print(f"RuntimeError: {self.core.exit_message}")
            sys.exit(1)


def run_terminal():
    load_env_file()
    if not get_api_key():
        print("No OPENAI_API_KEY found. Set it in .env or environment.")
        return
    core = MiraCore()
    terminal = MiraTerminal(core)
    core.run_terminal(terminal)
