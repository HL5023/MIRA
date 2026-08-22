#!/usr/bin/env python3
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

try:
    import curses
except ImportError:
    curses = None

from llm import LLM, load_env_file, get_api_key
from memory import Memory
from personality import Personality


def load_config(path: str = "config.json") -> dict:
    return json.loads(Path(path).read_text())


# Available slash commands: command -> description
def extract_facts(text: str) -> list:
    facts = []
    patterns = [
        r"[Mm]y name is ([^.]+)",
        r"[Ii] am ([^.]+)",
        r"[Ii]'m ([^.]+)",
        r"[Mm]y favorite ([^.]+) is ([^.]+)",
        r"[Ii] like ([^.]+)",
        r"[Ii] love ([^.]+)",
        r"[Ii] hate ([^.]+)",
        r"[Mm]y (?:mom|mother|dad|father|sister|brother) ([^.]+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            facts.append(match.group(0).strip())
    return facts


SPINNER = "|/-\\"


def spinner_for_frame(frame: int) -> str:
    return SPINNER[(frame // 2) % len(SPINNER)]


def typing_text(frame: int) -> str:
    return f"{spinner_for_frame(frame)}  Mira Is Typing..."


# Face animation timings in ms for the 5-frame cycles.
FACE_TIMING = [400, 400, 400, 400, 400]


def _face_frame(elapsed_ms: int, timings: list) -> int:
    total = sum(timings)
    pos = elapsed_ms % total
    acc = 0
    for i, t in enumerate(timings):
        acc += t
        if pos < acc:
            return i
    return 0


def face_for_mood(mood: str, elapsed_ms: int = 0) -> str:
    # Per-mood 5-frame animated faces. Cycle repeats every ~2900 ms.
    sequences = {
        "default": [
            '(‿•)',
            '(‿·)',
            '(‿◦)',
            '(‿·)',
            '(‿•)',
        ],

        "happy": [
            '(‿•)',
            '(‿✦)',
            '(‿◕)',
            '(‿✧)',
            '(‿•)',
        ],

        "curious": [
            '(‿•)',
            '(‿⊙)',
            '(‿◎)',
            '(‿◉)',
            '(‿•)',
        ],

        "mischievous": [
            '(¬•)',
            '(¬◉)',
            '(¬✦)',
            '(¬◉)',
            '(¬•)',
        ],

        "annoyed": [
            '(︵•)',
            '(︵¬)',
            '(︵╬)',
            '(︵≖)',
            '(︵¬)',
        ],

        "angry": [
            '(︵╬)',
            '(︵•)',
            '(︵╬)',
            '(︵◉)',
            '(︵╬)',
        ],

        "sad": [
            '(︵•)',
            '(︵·)',
            '(︵╥)',
            '(︵·)',
            '(︵•)',
        ],
    }
    seq = sequences.get(mood, sequences["default"])
    return seq[_face_frame(elapsed_ms, FACE_TIMING)]

class Companion:
    def __init__(self):
        self.config = load_config()
        self.personality = Personality()
        self.memory = Memory()
        # Chat history now persists unless user clears it
        self.llm = LLM()
        self.recent_inputs = []
        self.last_user_messages = []

        # UI state
        self.stdscr = None
        self.header_win = None
        self.chat_win = None
        self.status_win = None
        self.input_win = None
        self.chat_lines = []
        self.screen_height = 0
        self.screen_width = 0
        self.face_frame = 0
        self.input_buffer = ""
        self.typing_state = None  # "typing", "thinking", or None
        self.session_start = time.time()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.mood_color_pairs = {}

        # Shutdown state
        self.exit_message = None

        # Batch messaging
        self.pending_batch = []
        self.batch_deadline = None
        self.batch_delay = 2.0

        # Async
        self.running = True
        self.user_queue = Queue()
        self.mira_queue = Queue()
        self.mira_thread = None
        self.idle_thread = None
        self.last_user_time = time.time()

    def save_fact_from_user(self, text: str):
        facts = extract_facts(text)
        for fact in facts:
            self.memory.remember_fact("user", fact)

    def _update_repetition(self, user_input: str) -> int:
        # Disabled: the AI kept hallucinating repetition, so we no longer tell it about repeats.
        return 0

    def build_prompt(self, user_input: str, context: str = None) -> str:
        state = self.personality.state()
        recent = self.memory.recent_interactions(5, session_id=self.session_id)
        facts = self.memory.facts_about("user")

        return self.llm.build_prompt(
            name=state["name"],
            voice=self.personality.voice,
            traits=self.personality.traits,
            mood=state["mood"],
            energy=state["energy"],
            patience=state["patience"],
            recent=recent,
            facts=facts,
            user_input=user_input,
            context=context,
        )

    def build_messages(self, user_input: str, context: str = None) -> list:
        state = self.personality.state()
        recent = self.memory.recent_interactions(5, session_id=self.session_id)
        facts = self.memory.facts_about("user")

        return self.llm.build_messages(
            name=state["name"],
            voice=self.personality.voice,
            traits=self.personality.traits,
            mood=state["mood"],
            energy=state["energy"],
            patience=state["patience"],
            recent=recent,
            facts=facts,
            user_input=user_input,
            context=context,
        )

    def _is_time_query(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        patterns = [
            r"\bwhat\s+time\b",
            r"\bcheck\s+(?:the\s+)?time\b",
            r"\btime\s+(?:rn|now|right\s+now|please)\b",
            r"\bcurrent\s+time\b",
            r"\bsystem\s+(?:clock|time)\b",
            r"\btell\s+me\s+(?:the\s+)?time\b",
        ]
        return any(re.search(p, lower) for p in patterns)

    def _get_time(self) -> str:
        return datetime.now().strftime("%I:%M %p")

    def _detect_insult(self, user_input: str) -> float:
        """Return annoyance amount based on bad words in the message."""
        lower = user_input.lower()
        directed_at_mira = any(p in lower for p in ["you ", "u ", "mira", "your ", "ur "])

        # Non-profane rude words
        rude_words = [
            "stupid", "idiot", "dumb", "freak", "jerk", "weirdo", "moron", "loser",
            "shut up", "gtfo", "worthless", "fuck", "fuck off", "bitch", "asshole", "dickhead"
        ]
        hits = sum(1 for w in rude_words if w in lower)

        if hits == 0:
            return 0.0

        # Directed insults are more annoying
        amount = min(0.5, hits * 0.15)
        if directed_at_mira:
            amount *= 1.5

        if user_input.isupper() and len(user_input) > 3:
            amount += 0.1

        return min(0.5, amount)

    def respond(self, user_input: str) -> list:
        """Process user input and return list of (sender, text) messages."""
        self.memory.log_interaction("user", user_input, session_id=self.session_id)
        self.save_fact_from_user(user_input)
        annoy_amount = self._detect_insult(user_input)
        self.personality.interact(recover_patience=(annoy_amount == 0))
        self.personality.drain_energy()

        if annoy_amount > 0:
            self.personality.annoy(annoy_amount, "insult")

        output = []

        # Shut down when out of energy
        if self.personality.energy <= 0:
            self._generate_close_message("tired")
            raise RuntimeError("Mira is too tired and shut down")

        # Shut down when already in a bad mood and pushed too far
        close_thr = self.config["patience"]["close_terminal_threshold"]
        if self.personality.mood in ("angry", "annoyed") and self.personality.patience <= close_thr:
            self._generate_close_message("angry")
            raise RuntimeError("Mira got fed up and shut down")

        # Time check (only "tool" in v0.1)
        context = None
        if self._is_time_query(user_input):
            context = f"the current time is {self._get_time()}"

        # Get AI response
        try:
            if self.llm.provider == "openai":
                messages = self.build_messages(user_input, context)
                reply = self.llm.generate(messages=messages)
            else:
                prompt = self.build_prompt(user_input, context)
                reply = self.llm.generate(prompt=prompt)
            if not reply:
                raise RuntimeError("AI returned empty response")
            reply = self.llm.clean_reply(reply)
        except Exception as e:
            reply = f"(AI failed: {e})"

        for line in reply.split("\n"):
            line = line.strip()
            if line:
                output.append((self.personality.name, line))

        # Log Mira's main reply
        full_reply = "\n".join([line for _, line in output if _ == self.personality.name and "*" not in line])
        if full_reply:
            self.memory.log_interaction("companion", full_reply, session_id=self.session_id)

        self.personality.update(user_input)
        return output



    # ── UI Methods ─────────────────────────────────────────────────────────

    def _init_curses(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(1)
        self._init_colors()
        self.stdscr.clear()
        self._resize()

    def _init_colors(self):
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        # pair numbers: fg, bg (-1 = default bg)
        curses.init_pair(1, curses.COLOR_CYAN, -1)    # Mira fallback
        curses.init_pair(2, curses.COLOR_GREEN, -1)   # User
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # System / tool
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # Status
        curses.init_pair(5, curses.COLOR_WHITE, -1)   # Header
        curses.init_pair(6, curses.COLOR_RED, -1)     # Angry/alert

        # Per-mood colors for Mira's chat text
        mood_colors = {
            "normal": curses.COLOR_WHITE,
            "happy": curses.COLOR_YELLOW,
            "curious": curses.COLOR_CYAN,
            "mischievous": curses.COLOR_MAGENTA,
            "annoyed": curses.COLOR_GREEN,
            "angry": curses.COLOR_RED,
            "sad": curses.COLOR_BLUE,
        }
        self.mood_color_pairs = {}
        for i, (mood, color) in enumerate(mood_colors.items(), start=10):
            curses.init_pair(i, color, -1)
            self.mood_color_pairs[mood] = curses.color_pair(i)

    def _resize(self):
        self.screen_height, self.screen_width = self.stdscr.getmaxyx()
        if self.screen_height < 6 or self.screen_width < 20:
            return

        # Header at top, chat in middle, input + status at bottom
        self.header_win = curses.newwin(1, self.screen_width, 0, 0)
        self.chat_win = curses.newwin(self.screen_height - 3, self.screen_width, 1, 0)
        self.input_win = curses.newwin(1, self.screen_width, self.screen_height - 2, 0)
        self.status_win = curses.newwin(1, self.screen_width, self.screen_height - 1, 0)
        self.input_win.timeout(100)

        self._draw_header()
        self._redraw_chat()
        self._update_status()

    def _wrap(self, text: str, width: int) -> list:
        lines = []
        while text:
            if len(text) <= width:
                lines.append(text)
                break
            # find break point
            break_at = text.rfind(" ", 0, width + 1)
            if break_at <= 0:
                break_at = width
            lines.append(text[:break_at])
            text = text[break_at:].lstrip()
        return lines if lines else [""]

    def _redraw_chat(self):
        if not self.chat_win:
            return
        self.chat_win.clear()
        max_y = self.screen_height - 3

        # Build a flat list of (text, color) in chronological order, keep only what fits.
        rendered = []
        for sender, mood, line in self.chat_lines:
            if sender == self.personality.name:
                color = self.mood_color_pairs.get(mood, curses.color_pair(1))
            elif sender == "You":
                color = curses.color_pair(2)
            elif sender is None:
                color = curses.color_pair(3)
            else:
                color = curses.color_pair(3)
            for sub in self._wrap(line, self.screen_width - 1):
                rendered.append((sub, color))

        # Show the most recent lines that fit; render from top.
        visible = rendered[-max_y:]
        for y, (sub, color) in enumerate(visible):
            try:
                self.chat_win.addnstr(y, 0, sub, self.screen_width - 1, color)
            except curses.error:
                pass
        self.chat_win.refresh()

    def _chat_message(self, sender: str, text: str):
        timestamp = datetime.now().strftime("%H:%M")
        # Store as tuples (sender, mood_at_send, display_text) for colored rendering.
        mood_at_send = self.personality.mood if sender == self.personality.name else None
        for sub in str(text).split("\n"):
            if not sub.strip():
                continue
            if sender == self.personality.name:
                line = f"[{timestamp}] {self.personality.name}: {sub}"
            elif sender == "You":
                line = f"[{timestamp}] You: {sub}"
            elif sender is None:
                line = f"[{timestamp}] {sub}"
            else:
                line = f"[{timestamp}] {sender}: {sub}"
            self.chat_lines.append((sender, mood_at_send, line))
        self._redraw_chat()
        self._update_status()

    def _draw_header(self):
        if not self.header_win:
            return
        header = " Mira INDEV  |  Press ESC To Quit "
        header = header[: self.screen_width - 1]
        try:
            self.header_win.clear()
            self.header_win.addnstr(0, 0, header, self.screen_width - 1, curses.color_pair(5) | curses.A_BOLD)
            self.header_win.refresh()
        except curses.error:
            pass

    def _update_status(self):
        if not self.status_win:
            return
        self.face_frame += 1
        state = self.personality.state()
        elapsed_ms = int((time.time() - self.session_start) * 1000)
        face = face_for_mood(state["mood"], elapsed_ms)
        time_str = datetime.now().strftime("%H:%M")

        if self.typing_state in ("typing", "thinking"):
            indicator = typing_text(self.face_frame)
        else:
            indicator = None

        elapsed = time.time() - self.session_start
        hours, rem = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            duration = f"{hours}h{minutes:02d}m"
        else:
            duration = f"{minutes}m{seconds:02d}s"

        status_text = f"{face}  {state['mood'].capitalize()}  |  Energy {state['energy']:.0%}  |  Patience {state['patience']:.0%}  |  {duration}  |  {time_str}"
        if indicator:
            status_text = f"{indicator}  |  " + status_text
        status_text = status_text[: self.screen_width - 1]
        try:
            self.status_win.clear()
            self.status_win.addnstr(0, 0, status_text, self.screen_width - 1, curses.color_pair(4))
            self.status_win.refresh()
        except curses.error:
            pass
        # keep header in sync so the face animates there too
        self._draw_header()

    def _draw_input(self):
        if not self.input_win:
            return
        self.input_win.clear()
        prompt = f"You > {self.input_buffer}"
        try:
            self.input_win.addnstr(0, 0, prompt, self.screen_width - 1, curses.color_pair(2) | curses.A_BOLD)
        except curses.error:
            pass
        self.input_win.refresh()

    # ── Async workers ───────────────────────────────────────────────────────

    def _mira_worker(self):
        while self.running:
            try:
                user_input = self.user_queue.get(timeout=0.5)
            except Empty:
                continue
            self.last_user_time = time.time()

            # initial typing pause
            self.typing_state = "typing"
            self._update_status()
            time.sleep(random.uniform(1, 2))

            # generate reply
            self.typing_state = "thinking"
            self._update_status()
            try:
                messages = self.respond(user_input)
            except RuntimeError:
                # Mira chose to shut down
                self.mira_queue.put((None, "_close_terminal"))
                return

            # typing delay based on Mira's reply length
            reply_text = " ".join([text for _, text in messages if _ == self.personality.name])
            word_count = len(reply_text.split())
            delay = min(10, max(3, word_count * 0.5 + random.uniform(0, 2)))

            self.typing_state = "typing"
            self._update_status()
            start = time.time()
            while time.time() - start < delay:
                if not self.running:
                    return
                time.sleep(0.1)

            self.typing_state = None
            for sender, text in messages:
                self.mira_queue.put((sender, text))

    def _idle_worker(self):
        # Run every 10 seconds: recover energy when idle, ghosting checks every 60s.
        while self.running:
            time.sleep(10)
            if not self.running:
                return
            if not self.user_queue.empty():
                self.last_user_time = time.time()
                continue
            if self.typing_state:
                self.last_user_time = time.time()
                continue

            # Recover 1% energy every 10 idle seconds
            self.personality.recover_energy_idle(0.01)

            elapsed = time.time() - self.last_user_time

            # Ghosting checks every 60 seconds
            if int(elapsed) % 60 != 0:
                continue

            # Ghosting makes her slowly more annoyed after 10 min
            if elapsed > 600 and random.random() < 0.05:
                self.personality.annoy(0.03, "ghosted")

            # Very angry and ghosted: might leave
            if self.personality.mood == "angry" and elapsed > 600:
                if random.random() < 0.3:
                    self.mira_queue.put((self.personality.name, random.choice([
                        "u ghosted me... fine. im out.", "im done waiting"
                    ])))
                    self.mira_queue.put((None, "_close_terminal"))
                    return

            # Angry: complain only after some silence
            if self.personality.mood == "angry" and elapsed > 180:
                if random.random() < 0.1:
                    self.mira_queue.put((self.personality.name, random.choice([
                        "wow ok ignore me", ":/", "u suck", "im done waiting"
                    ])))
            # Just checking if still there after long silence
            elif elapsed > 1200:  # >20 min
                if random.random() < 0.1:
                    self.mira_queue.put((self.personality.name, random.choice([
                        "u still there?", "hello?", "...", "u alive?"
                    ])))

    def _generate_greeting(self):
        # Sometimes Mira just stays silent at startup
        if random.random() < 0.25:
            return
        try:
            if self.llm.provider == "openai":
                messages = self.build_messages("")
                reply = self.llm.generate(messages=messages)
            else:
                prompt = self.build_prompt("")
                reply = self.llm.generate(prompt=prompt)
            if reply:
                reply = self.llm.clean_reply(reply)
                for line in reply.split("\n"):
                    if line.strip():
                        self.mira_queue.put((self.personality.name, line.strip()))
        except Exception as e:
            self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))

    def _generate_close_message(self, mood: str):
        prompts = {
            "angry": "im leaving",
            "sad": "im tired",
            "tired": "im too tired",
        }
        try:
            ui = prompts.get(mood, "bye")
            if self.llm.provider == "openai":
                messages = self.build_messages(ui)
                reply = self.llm.generate(messages=messages)
            else:
                prompt = self.build_prompt(ui)
                reply = self.llm.generate(prompt=prompt)
            reply = self.llm.clean_reply(reply)
            for line in reply.split("\n"):
                if line.strip():
                    self.mira_queue.put((self.personality.name, line.strip()))
        except Exception:
            pass
        self.mira_queue.put((None, "_close_terminal"))

    def _generate_goodbye(self):
        try:
            if self.llm.provider == "openai":
                messages = self.build_messages("bye")
                reply = self.llm.generate(messages=messages)
            else:
                prompt = self.build_prompt("bye")
                reply = self.llm.generate(prompt=prompt)
            reply = self.llm.clean_reply(reply)
            for line in reply.split("\n"):
                if line.strip():
                    self.mira_queue.put((self.personality.name, line.strip()))
        except Exception as e:
            self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))

    def _generate_facts_reply(self):
        try:
            if self.llm.provider == "openai":
                messages = self.build_messages("tell me what you remember")
                reply = self.llm.generate(messages=messages)
            else:
                prompt = self.build_prompt("tell me what you remember")
                reply = self.llm.generate(prompt=prompt)
            reply = self.llm.clean_reply(reply)
            for line in reply.split("\n"):
                if line.strip():
                    self.mira_queue.put((self.personality.name, line.strip()))
        except Exception as e:
            self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))

    def _close_terminal(self):
        # Let curses.wrapper clean up the terminal, then raise outside.
        self.exit_message = "Mira got fed up and shut down"
        self.running = False

    def _flush_batch(self):
        if not self.pending_batch:
            self.batch_deadline = None
            return
        combined = " ".join(self.pending_batch)
        self.pending_batch = []
        self.batch_deadline = None
        self.user_queue.put(combined)

    def _refresh_ui(self):
        self._redraw_chat()
        self._update_status()

    # ── Run ────────────────────────────────────────────────────────────────

    def run(self):
        if curses is None:
            print("curses not available, falling back to simple mode")
            self.run_simple()
            return
        try:
            curses.wrapper(self._run_curses)
        finally:
            # Force terminal reset to prevent escape sequences leaking
            try:
                curses.endwin()
            except Exception:
                pass
            print("\033c", end="")
            sys.stdout.flush()
        if self.exit_message:
            print("\n*creates traceback*")
            raise RuntimeError(self.exit_message)

    def _run_curses(self, stdscr):
        self._init_curses(stdscr)

        # Start workers
        self.mira_thread = threading.Thread(target=self._mira_worker, daemon=True)
        self.idle_thread = threading.Thread(target=self._idle_worker, daemon=True)
        self.mira_thread.start()
        self.idle_thread.start()

        # Greeting generated by AI in background
        threading.Thread(target=self._generate_greeting, daemon=True).start()

        while self.running:
            try:
                # Keep animations moving even while waiting for input
                self._update_status()

                # Process Mira messages
                while not self.mira_queue.empty():
                    sender, text = self.mira_queue.get_nowait()
                    if text == "_close_terminal":
                        self._close_terminal()
                        return
                    self._chat_message(sender, text)

                # Handle input
                try:
                    ch = self.input_win.getch()
                except curses.error:
                    ch = -1

                # Check if batch is ready to send even if no key pressed
                if self.batch_deadline and time.time() >= self.batch_deadline:
                    self._flush_batch()

                if ch == -1:
                    continue

                if ch in (10, 13):  # Enter
                    if self.input_buffer.strip():
                        user_input = self.input_buffer.strip()
                        self.input_buffer = ""
                        self._draw_input()

                        if user_input.lower() in ("quit", "exit", "bye"):
                            threading.Thread(target=self._generate_goodbye, daemon=True).start()
                            self.running = False
                            break

                        # Add to batch instead of sending immediately
                        self._chat_message("You", user_input)
                        self.pending_batch.append(user_input)
                        self.batch_deadline = time.time() + self.batch_delay

                elif ch in (127, curses.KEY_BACKSPACE, 263):
                    self.input_buffer = self.input_buffer[:-1]
                    self._draw_input()

                elif ch == 27:  # ESC
                    self.running = False
                    break

                elif 32 <= ch < 127:
                    self.input_buffer += chr(ch)
                    self._draw_input()

            except KeyboardInterrupt:
                self._chat_message(self.personality.name, "rude :/")
                self.running = False
                break
            except curses.error:
                break

        self.running = False

    def run_simple(self):
        # Simple fallback: no top greeting, status shown at bottom with prompt.
        while True:
            try:
                self.face_frame += 1
                state = self.personality.state()
                face = face_for_mood(state["mood"], self.face_frame)
                print(f"\n{face} {state['mood']} | energy {state['energy']:.0%} | patience {state['patience']:.0%} | {datetime.now().strftime('%H:%M')}")
                user_input = input("you > ").strip()
                if not user_input:
                    continue

                if user_input.lower() in ("quit", "exit", "bye"):
                    print(f"[{self.personality.name}] bye then :3")
                    break

                if user_input.lower() == "status":
                    print(f"[{state['name']}] mood={state['mood']}, energy={state['energy']}, patience={state['patience']}")
                    continue

                if user_input.lower() == "facts":
                    facts = self.memory.all_facts()
                    if facts:
                        print(f"[{self.personality.name}] i remember:")
                        for f in facts:
                            print(f"  - {f}")
                    else:
                        print(f"[{self.personality.name}] not much yet")
                    continue

                # typing delay
                time.sleep(random.uniform(3, 8))
                messages = self.respond(user_input)
                for sender, text in messages:
                    print(f"[{self.personality.name}] {text}")

            except KeyboardInterrupt:
                print(f"\n[{self.personality.name}] rude :/")
                break


def main():
    load_env_file()
    if not os.environ.get("OPENAI_API_KEY"):
        api_key = get_api_key()
        os.environ["OPENAI_API_KEY"] = api_key
    companion = Companion()
    companion.run()


if __name__ == "__main__":
    main()
