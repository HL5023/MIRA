import curses
import math
import random
import time
import unicodedata
from datetime import datetime


# ── Face sequences (one-line kaomoji, always two eyes) ─────────────────────
FACE_SEQUENCES = {
    "normal": [
        "(•ᴗ•)",
        "(·ᴗ·)",
        "(◦ᴗ◦)",
        "(·ᴗ·)",
        "(•ᴗ•)",
    ],
    "happy": [
        "(•ᴗ•)",
        "(✦ᴗ✦)",
        "(◕ᴗ◕)",
        "(✧ᴗ✧)",
        "(•ᴗ•)",
    ],
    "curious": [
        "(•ᴗ•)",
        "(⊙ᴗ⊙)",
        "(◎ᴗ◎)",
        "(◉ᴗ◉)",
        "(•ᴗ•)",
    ],
    "mischievous": [
        "(•ᴗ•)",
        "(¬ᴗ¬)",
        "(◕ᴗ◕)",
        "(¬ᴗ¬)",
        "(•ᴗ•)",
    ],
    "annoyed": [
        "(•︿•)",
        "(¬︿¬)",
        "(≖︿≖)",
        "(¬︿¬)",
        "(•︿•)",
    ],
    "angry": [
        "(╬︿╬)",
        "(≖︿≖)",
        "(╬︿╬)",
        "(ಠ︿ಠ)",
        "(╬︿╬)",
    ],
    "sad": [
        "(•﹏•)",
        "(◔﹏◔)",
        "(╥﹏╥)",
        "(╥﹏╥)",
        "(•﹏•)",
    ],
}


# ── Per-mood frame timings (milliseconds) ────────────────────────────────────
FACE_TIMING = {
    "normal": [500, 500, 500, 500, 500],
    "happy": [350, 350, 350, 350, 350],
    "curious": [400, 400, 400, 400, 400],
    "mischievous": [450, 450, 450, 450, 450],
    "annoyed": [550, 550, 550, 550, 550],
    "angry": [550, 550, 550, 550, 550],
    "sad": [750, 750, 750, 750, 750],
}


# ── Particle symbols ───────────────────────────────────────────────────────
PARTICLE_SYMBOLS = ["·", "｡", "˚", "⁺", "✧"]


class Particle:
    __slots__ = ("x", "y", "symbol", "vx", "vy", "age", "lifetime")

    def __init__(self, x, y, symbol, vx, vy, lifetime=None):
        self.x = float(x)
        self.y = float(y)
        self.symbol = symbol
        self.vx = vx
        self.vy = vy
        self.age = 0.0
        self.lifetime = lifetime

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.age += dt
        if self.lifetime and self.age > self.lifetime:
            return False
        return True


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
        self.face_elapsed_ms = 0
        self.typing_frame = 0
        self.last_render = time.time()
        self.particles = []

        self._init_colors()
        try:
            curses.curs_set(0)
        except Exception:
            pass
        self._seed_particles()

    def _init_colors(self):
        if not curses.has_colors():
            self.mood_color_pairs = {}
            return
        curses.start_color()
        curses.use_default_colors()

        # Basic pairs
        curses.init_pair(1, curses.COLOR_WHITE, -1)   # default / face
        curses.init_pair(2, curses.COLOR_GREEN, -1)   # user
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # tools / typing indicator
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # status
        curses.init_pair(5, curses.COLOR_CYAN, -1)    # header
        curses.init_pair(6, curses.COLOR_RED, -1)     # mood label (unique, not used by UI or user)
        curses.init_pair(7, curses.COLOR_BLUE, -1)    # sad messages

        # Per-mood chat colors (face stays neutral, only chat text is tinted per message)
        self.mood_color_pairs = {
            "normal": curses.color_pair(1),       # white
            "happy": curses.color_pair(3),        # yellow
            "curious": curses.color_pair(5),      # cyan
            "mischievous": curses.color_pair(4),  # magenta
            "annoyed": curses.color_pair(2),      # green
            "angry": curses.color_pair(6),        # red
            "sad": curses.color_pair(7),          # blue
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

    def _seed_particles(self, count=4):
        self.particles = []
        for _ in range(count):
            self.particles.append(self._random_particle())

    def _random_particle(self):
        # Place around where Mira will be; actual screen coords updated in resize
        x = random.uniform(-8, 8)
        y = random.uniform(-3, 3)
        symbol = random.choice(PARTICLE_SYMBOLS)
        vx = random.uniform(-0.3, 0.3)
        vy = random.uniform(-0.15, 0.15)
        return Particle(x, y, symbol, vx, vy, lifetime=random.uniform(4.0, 8.0))

    def resize(self):
        self.screen_height, self.screen_width = self.stdscr.getmaxyx()
        if self.screen_height < 6 or self.screen_width < 20:
            return

        # Layout
        self.header_win = curses.newwin(2, self.screen_width, 0, 0)

        # Mira area: at least 5 rows, more if terminal is tall
        mira_height = max(5, min(8, self.screen_height // 4))
        self.mira_win = curses.newwin(mira_height, self.screen_width, 2, 0)

        # Status at bottom, input above it, chat in between
        self.status_win = curses.newwin(1, self.screen_width, self.screen_height - 1, 0)
        self.input_win = curses.newwin(1, self.screen_width, self.screen_height - 2, 0)

        chat_y = 2 + mira_height
        chat_height = max(1, self.screen_height - chat_y - 2)
        self.chat_win = curses.newwin(chat_height, self.screen_width, chat_y, 0)

        self.stdscr.clear()

    @staticmethod
    def _display_width(s: str) -> int:
        width = 0
        for ch in s:
            if unicodedata.east_asian_width(ch) in ("F", "W"):
                width += 2
            else:
                width += 1
        return width

    def _face_for_mood(self, mood: str, elapsed_ms: int) -> str:
        seq = FACE_SEQUENCES.get(mood, FACE_SEQUENCES["normal"])
        timings = FACE_TIMING.get(mood, [400] * 5)
        total = sum(timings)
        if total == 0:
            return seq[0]
        pos = elapsed_ms % total
        acc = 0
        for i, t in enumerate(timings):
            acc += t
            if pos < acc:
                return seq[i]
        return seq[0]

    def _face_offset(self, mood: str, elapsed_ms: int) -> int:
        """Subtle horizontal movement based on mood."""
        t = elapsed_ms / 1000.0
        if mood == "happy":
            return int(math.sin(t * 3) * 1)
        if mood == "curious":
            return int(math.sin(t * 1.5) * 1)
        if mood == "mischievous":
            return int(math.sin(t * 2) * 1)
        if mood == "annoyed":
            return int(math.sin(t * 1) * 0)
        if mood == "angry":
            return int(math.sin(t * 12) * 1)
        if mood == "sad":
            return int(math.sin(t * 0.8) * 1)
        return 0

    def _update_particles(self, dt, mood):
        # Target particle count based on mood
        targets = {
            "normal": 4,
            "happy": 7,
            "curious": 5,
            "mischievous": 5,
            "annoyed": 2,
            "angry": 3,
            "sad": 2,
        }
        target = targets.get(mood, 4)

        # Update existing particles
        alive = []
        for p in self.particles:
            if p.update(dt):
                alive.append(p)
        self.particles = alive

        # Spawn new ones if below target
        while len(self.particles) < target:
            self.particles.append(self._random_particle())

        # Cap particles
        self.particles = self.particles[:target + 2]

    def _draw_header(self, state=None):
        if not self.header_win:
            return
        self.header_win.clear()
        left = " MIRA // V.26.2.1"
        line = left[:self.screen_width - 1]
        try:
            self.header_win.addnstr(0, 0, line, self.screen_width - 1, curses.color_pair(5))
        except curses.error:
            pass
        # separator
        sep = "─" * (self.screen_width - 1)
        try:
            self.header_win.addnstr(1, 0, sep, self.screen_width - 1, curses.color_pair(5))
        except curses.error:
            pass
        self.header_win.refresh()

    def _draw_mira(self, state):
        if not self.mira_win:
            return
        self.mira_win.clear()
        h, w = self.mira_win.getmaxyx()
        if h < 3 or w < 12:
            self.mira_win.refresh()
            return

        mood = state.get("mood", "normal")
        elapsed_ms = int(state.get("face_elapsed_ms", self.face_elapsed_ms))
        mode = state.get("mode", "idle")
        typing = mode in ("typing", "thinking")

        # Center of Mira area
        cx = w // 2
        cy = max(1, h // 2 - 1)

        # Mood label and face use the current mood color
        mood_color = self.mood_color_pairs.get(mood, curses.color_pair(1))
        mood_label = " " + mood.capitalize()
        try:
            self.mira_win.addnstr(cy - 1, max(0, cx - self._display_width(mood_label) // 2), mood_label, w - 1, mood_color | curses.A_BOLD)
        except curses.error:
            pass

        # Face with subtle horizontal offset
        face = self._face_for_mood(mood, elapsed_ms)
        offset = self._face_offset(mood, elapsed_ms)
        face_x = cx - self._display_width(face) // 2 + offset
        try:
            self.mira_win.addnstr(cy, face_x, face, w - 1, mood_color)
        except curses.error:
            pass

        # Typing / thinking indicator
        if typing:
            self._draw_typing_indicator(cy, cx, w, h)

        self.mira_win.refresh()

    def _rect_orbit(self, t, rx, ry):
        """Return a point on a rectangle with constant linear speed."""
        perimeter = 2 * (rx + ry)
        pos = (t % (2 * math.pi)) / (2 * math.pi) * perimeter

        if pos < rx:
            # top side, moving right
            x = -rx + pos
            y = -ry
        elif pos < rx + ry:
            # right side, moving down
            x = rx
            y = -ry + (pos - rx)
        elif pos < 2 * rx + ry:
            # bottom side, moving left
            x = rx - (pos - (rx + ry))
            y = ry
        else:
            # left side, moving up
            x = -rx
            y = ry - (pos - (2 * rx + ry))
        return x, y

    def _draw_radio(self, cx, cy, w):
        bars = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
        row = cy - 1
        if row < 0:
            return
        # Three tightly-spaced bars that animate in a wave
        for i, offset in enumerate((-1, 0, 1)):
            phase = self.typing_frame * 0.35 + i * 1.2
            level = int((math.sin(phase) + 1) / 2 * (len(bars) - 1))
            ch = bars[level]
            x = cx + offset
            if 0 <= x < w:
                try:
                    self.mira_win.addch(row, x, ch, curses.color_pair(3))
                except curses.error:
                    pass

    def _draw_typing_indicator(self, cy, cx, w, h):
        text = " MIRA IS TYPING"
        dot_frames = [
            " · · ·",
            " ｡ · ·",
            " · ｡ ·",
            " · · ｡",
        ]
        dots = dot_frames[self.typing_frame % len(dot_frames)]
        text_y = cy + 2
        dots_y = cy + 3

        # Delay the typing text very slightly so it doesn't pop in
        if self.typing_frame < 2:
            return text_y, dots_y, len(text)

        # Draw text first, then the moving dots underneath it
        if text_y < h:
            text_x = max(0, cx - len(text) // 2)
            if text_x + len(text) < w:
                try:
                    self.mira_win.addnstr(text_y, text_x, text, w - 1, curses.color_pair(3))
                except curses.error:
                    pass

        if dots_y < h:
            dots_x = max(0, cx - len(dots) // 2)
            if dots_x + len(dots) < w:
                try:
                    self.mira_win.addnstr(dots_y, dots_x, dots, w - 1, curses.color_pair(3))
                except curses.error:
                    pass

        return text_y, dots_y, len(text)

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

        # Keep only the most recent whole units that fit in the available height
        visible = []
        remaining = h
        for unit in reversed(units):
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
        energy = int(state.get("energy", 1.0) * 100)
        patience = int(state.get("patience", 1.0) * 100)
        duration = self._format_duration(state.get("session_start", time.time()))
        time_str = datetime.now().strftime("%H:%M")
        text = f"Energy {energy}%     Patience {patience}%     {duration}     {time_str}"
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

        # Update face animation timing
        self.face_elapsed_ms += int(dt * 1000)
        if mode in ("typing", "thinking"):
            self.typing_frame += 1

        # Ambient particles and radio removed by request.

        self._draw_header(state)
        self._draw_mira(state)
        self._draw_chat(state.get("chat_lines", []), state.get("mood", "normal"))
        self._draw_status(state)

    def cleanup(self):
        try:
            curses.curs_set(1)
        except Exception:
            pass
