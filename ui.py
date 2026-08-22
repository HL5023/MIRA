import curses
import time
from datetime import datetime


class MiraRenderer:
    def __init__(self, stdscr, personality):
        self.stdscr = stdscr
        self.personality = personality
        self.screen_height = 0
        self.screen_width = 0

        # UI sub-windows
        self.header_win = None
        self.mira_win = None
        self.chat_win = None
        self.input_win = None
        self.status_win = None

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
            return
        curses.start_color()
        curses.use_default_colors()

        # Basic pairs
        curses.init_pair(1, curses.COLOR_WHITE, -1)   # default
        curses.init_pair(2, curses.COLOR_GREEN, -1)   # user
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # tools / typing indicator
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # status
        curses.init_pair(5, curses.COLOR_CYAN, -1)    # header
        curses.init_pair(6, curses.COLOR_RED, -1)     # mood label
        curses.init_pair(7, curses.COLOR_BLUE, -1)    # sad messages

        # Per-mood chat color (only Mira's chat text is tinted)
        self.mood_color_pairs = {
            "normal": curses.color_pair(1),
            "happy": curses.color_pair(3),
            "curious": curses.color_pair(5),
            "mischievous": curses.color_pair(4),
            "annoyed": curses.color_pair(2),
            "angry": curses.color_pair(6),
            "sad": curses.color_pair(7),
        }

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
        if self.screen_height < 6 or self.screen_width < 20:
            return

        # Layout: header, then full-terminal chat, then input + status
        self.header_win = curses.newwin(2, self.screen_width, 0, 0)

        # Status at bottom, input above it, chat fills the rest
        self.status_win = curses.newwin(1, self.screen_width, self.screen_height - 1, 0)
        self.input_win = curses.newwin(1, self.screen_width, self.screen_height - 2, 0)

        chat_y = 2
        chat_height = max(1, self.screen_height - chat_y - 2)
        self.chat_win = curses.newwin(chat_height, self.screen_width, chat_y, 0)

        self.stdscr.clear()

    def _display_width(self, s: str) -> int:
        width = 0
        for ch in s:
            # Best-effort width for East Asian characters
            if ord(ch) > 127:
                width += 2
            else:
                width += 1
        return width

    def _draw_header(self, state=None):
        if not self.header_win:
            return
        self.header_win.clear()
        left = " MIRA // V.26.2.2"
        line = left[:self.screen_width - 1]
        try:
            self.header_win.addnstr(0, 0, line, self.screen_width - 1, curses.color_pair(5))
        except curses.error:
            pass
        sep = "─" * (self.screen_width - 1)
        try:
            self.header_win.addnstr(1, 0, sep, self.screen_width - 1, curses.color_pair(5))
        except curses.error:
            pass
        self.header_win.refresh()


    def _wrap(self, text, width):
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

        mood_color = self.mood_color_pairs.get(mood, curses.color_pair(1))

        # Build message units in chronological order, each unit is [label, message rows...]
        units = []
        for item in chat_lines:
            if isinstance(item, tuple) and len(item) == 3:
                sender, text, msg_mood = item
            else:
                sender, text = item
                msg_mood = None

            if sender == self.personality.name:
                label_color = self.mood_color_pairs.get(msg_mood, curses.color_pair(1))
                text_color = label_color
                label = f"{datetime.now().strftime('%H:%M')}  MIRA"
            elif sender == "You":
                label_color = curses.color_pair(2) | curses.A_BOLD
                text_color = curses.color_pair(2)
                label = f"{datetime.now().strftime('%H:%M')}  YOU"
            elif sender is None:
                units.append([("", curses.color_pair(3), 0), (f"[Tools] {text}", curses.color_pair(3), 0)])
                continue
            else:
                label_color = curses.color_pair(3)
                text_color = curses.color_pair(3)
                label = f"{datetime.now().strftime('%H:%M')}  {sender}"

            unit = []
            unit.append((label, label_color | curses.A_BOLD, 0))
            wrap_width = max(1, w - 5)
            wrapped = self._wrap(text, wrap_width)
            for i, sub in enumerate(wrapped):
                if i == 0:
                    unit.append((f"└─ {sub}", text_color, 2))
                else:
                    unit.append((f"   {sub}", text_color, 2))
            units.append(unit)

        # Clamp scroll offset to a sensible range
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

        # Remove the newest scroll_offset units, then take the most recent that fit
        candidates = units[:-self.scroll_offset] if self.scroll_offset else units[:]
        visible = []
        remaining = h
        for unit in reversed(candidates):
            if len(unit) > remaining:
                break
            visible.insert(0, unit)
            remaining -= len(unit)

        # Draw from top to bottom
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
        if not self.input_win:
            return
        self.input_win.clear()
        prompt = "YOU › "
        max_visible = max(1, self.screen_width - len(prompt) - 1)
        if len(buffer) > max_visible:
            visible = "..." + buffer[-(max_visible - 3):]
        else:
            visible = buffer
        line = f"{prompt}{visible}█"
        if len(line) > self.screen_width - 1:
            line = line[:self.screen_width - 1]
        try:
            self.input_win.addnstr(0, 0, line, self.screen_width - 1, curses.color_pair(2) | curses.A_BOLD)
        except curses.error:
            pass
        self.input_win.refresh()

    def _draw_status(self, state):
        if not self.status_win:
            return
        self.status_win.clear()
        mood = state.get("mood", "normal")
        energy = int(state.get("energy", 1.0) * 100)
        patience = int(state.get("patience", 1.0) * 100)
        duration = self._format_duration(state.get("session_start", time.time()))
        time_str = datetime.now().strftime("%H:%M")
        text = f"{mood.capitalize()} | Energy {energy}% | Patience {patience}% | {duration} | {time_str}"
        text = text[:self.screen_width - 1]
        try:
            self.status_win.addnstr(0, 0, text, self.screen_width - 1, curses.color_pair(4))
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
        dt = now - self.last_render
        self.last_render = now

        mode = state.get("mode", "idle")

        if mode in ("typing", "thinking"):
            self.typing_frame += 1

        self._draw_header(state)
        self._draw_chat(state.get("chat_lines", []), state.get("mood", "normal"))
        self._draw_status(state)

    def cleanup(self):
        try:
            curses.curs_set(1)
        except Exception:
            pass
