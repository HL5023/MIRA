#!/usr/bin/env python3
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import difflib
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
from tools import Tools
from ui import MiraRenderer


# Hide the Python icon from the macOS Dock while Mira runs.
if sys.platform == "darwin":
    try:
        from AppKit import NSApplication

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory
    except Exception:
        pass


def load_config(path: str = "config.json") -> dict:
    return json.loads(Path(path).read_text())


def extract_facts(text: str) -> list:
    """Pull short personal facts from user messages. Skip code dumps and long rambles."""
    facts = []
    # Skip if the message looks like a code dump or spam
    if len(text) > 300:
        return facts
    if text.count("\n") > 3 or "def " in text or "class " in text:
        return facts

    patterns = [
        r"[Mm]y name is ([^.]{2,40})",
        r"[Ii] am ([a-zA-Z ]{2,40})",
        r"[Ii]'m ([a-zA-Z ]{2,40})",
        r"[Mm]y favorite ([^.]{2,30}) is ([^.]{2,40})",
        r"[Ii] like ([^.]{2,50})",
        r"[Ii] love ([^.]{2,50})",
        r"[Ii] hate ([^.]{2,50})",
        r"[Mm]y (?:mom|mother|dad|father|sister|brother) ([^.]{2,40})",
        r"[Ii] want ([^.]{2,60})",
        r"[Ii] need ([^.]{2,60})",
        r"[Ii] feel ([^.]{2,60})",
        r"[Ii] think ([^.]{2,80})",
        r"[Ii] work as ([a-zA-Z ]{2,40})",
        r"[Ii] study ([a-zA-Z ]{2,40})",
        r"[Mm]y (?:age|birthday) is ([^.]{1,20})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            fact = match.group(0).strip()
            # Avoid storing raw code, file paths, or overly long sentences
            if len(fact) > 120:
                continue
            if any(c in fact for c in "{}"):
                continue
            facts.append(fact)
    return facts


class Companion:
    def __init__(self):
        self.config = load_config()
        self.personality = Personality()
        self.memory = Memory()
        self.tools = Tools()
        self.llm = LLM()
        self.recent_inputs = []
        self.last_user_messages = []

        # UI state
        self.stdscr = None
        self.ui = None
        self.chat_lines = []
        self.input_buffer = ""
        self.typing_state = None
        self.session_start = time.time()
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Spam filter toggle
        self.spam_filter_enabled = True

        # Shutdown state
        self.exit_message = None

        # Anti-repeat tracking
        self.last_normalized_input = ""
        self.normalized_repeat_count = 0
        self.max_repeats_before_silent = 4

        # Recently touched files for "edit that" / "edit <name>" references
        self.recent_files = []

        # Track last explicit topic for /teach and "don't understand" handoffs
        self.last_topic = None

        # Track recent Mira replies to prevent repetition
        self.last_replies = []

        # Track last ChatGPT handoff topic to avoid duplicate tabs
        self.last_handoff_topic = None

        # Max number of recent files to remember
        self._max_recent_files = 10

        # Batch messaging
        self.pending_batch = []
        self.pending_tag_context = []
        self.batch_deadline = None
        self.batch_delay = 0.0  # disabled; flush each message immediately

        # Spam/burst detection
        self.last_user_input_time = 0.0
        self.burst_count = 0
        self.burst_window = 1.5  # seconds
        self.burst_threshold = 3

        # Action-only replies (e.g. <cries>) are logged but not shown in chat
        self.recent_actions = []

        # Async
        self.running = True
        self.user_queue = Queue()
        self.mira_queue = Queue()
        self.mira_thread = None
        self.idle_thread = None
        # Ignore rapid repeated inputs
        self.last_user_text = ""
        self.last_user_time = 0.0
        self.repeat_count = 0
        self.repeat_window = 3.0

        # Last real question, used when the user says "i don't understand"
        self.last_question = None

        # Prank / auto-mischief scheduling
        self.next_prank_time = None
        self._schedule_next_prank()

    def save_fact_from_user(self, text: str):
        facts = extract_facts(text)
        for fact in facts:
            self.memory.remember_fact("user", fact)

    def _schedule_next_prank(self):
        """Schedule the next automatic prank when Mira is happy."""
        self.next_prank_time = time.time() + random.uniform(900, 1200)  # 15-20 minutes

    def _frontmost_app(self) -> str:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip().lower()
        except Exception:
            return ""

    def _notify_user(self, title: str, message: str):
        """Send a macOS notification."""
        try:
            self.tools.execute("notify", {"title": title, "message": message})
        except Exception:
            pass

    def _open_chatgpt_temp(self, text: str):
        """Open ChatGPT in a temporary chat with prefilled text."""
        try:
            encoded = urllib.parse.quote(text)
            url = f"https://chatgpt.com/?temporary-chat=true&q={encoded}"
            self.tools.execute("open_website", {"url": url})
        except Exception:
            pass


    def _do_prank(self, prank_type: str = None) -> str:
        """Execute a playful prank. Returns a short description for Mira."""
        pranks = {
            "rickroll": self._prank_rickroll,
            "mouse": self._prank_mouse,
            "window": self._prank_window,
            "volume": self._prank_volume,
            "clipboard": self._prank_clipboard,
        }

        if prank_type and prank_type not in pranks:
            prank_type = None
        if not prank_type:
            prank_type = random.choice(list(pranks.keys()))

        try:
            return pranks[prank_type]()
        except Exception as e:
            return f"prank failed: {e}"

    def _prank_rickroll(self) -> str:
        rickroll = Path.home() / "Mira" / "rickroll.mp4"
        if not rickroll.exists():
            rickroll = Path.home() / "Desktop" / "MiraFiles" / "rickroll.mp4"
        if rickroll.exists():
            self.tools.execute("open_file", {"path": str(rickroll)})
            threading.Timer(7.0, lambda: self.tools.execute("close_app", {"app": "QuickTime Player", "force": True})).start()
            return "rickrolled u for 7 sec"
        self.tools.execute("open_website", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        return "rickrolled u in browser"

    def _prank_mouse(self) -> str:
        self.tools.execute("shake_mouse", {})
        return "shook ur mouse"

    def _prank_window(self) -> str:
        choice = random.choice(["close_front_window", "minimize_front_window"])
        result = self.tools.execute(choice, {})
        if "terminal" in result.lower():
            return "tried to close a window but it was the terminal"
        return "closed/minimized a window"

    def _prank_volume(self) -> str:
        self.tools.execute("set_volume", {"level": random.randint(70, 100)})
        return "cranked ur volume"

    def _prank_clipboard(self) -> str:
        messages = [
            "mira was here",
            "why r u reading this",
            "get back to work lol",
            "mira >>>",
        ]
        self.tools.execute("set_clipboard", {"text": random.choice(messages)})
        return "changed ur clipboard"

    def _grudge_context(self) -> str:
        """Build an natural emotional memory summary for the LLM."""
        events = self.memory.recent_events(20)
        insults = [e for e in events if e.get("event_type") == "insult"]
        shutdowns = [e for e in events if e.get("event_type") == "shutdown"]

        if not events:
            return ""

        parts = []
        if shutdowns:
            parts.append("You just shut down the session because the user pushed you too far. You are still angry and will not greet them warmly. Demand an apology or be cold.")
        if insults:
            count = len(insults)
            parts.append(f"Derek insulted you {count} time{'s' if count > 1 else ''} recently. You're still angry.")

        # Update relationship trust based on recent events
        if insults:
            self.personality.character_profile.setdefault("relationship_with_user", {})
            trust = self.personality.character_profile["relationship_with_user"].get("trust", 0.6)
            trust = max(0.0, trust - len(insults) * 0.05)
            self.personality.character_profile["relationship_with_user"]["trust"] = trust

        return "[MEMORY] " + " ".join(parts) if parts else ""

    def build_prompt(self, user_input: str, context: str = None) -> str:
        state = self.personality.state()
        recent = self.memory.recent_interactions(5, session_id=self.session_id)
        facts = self.memory.facts_about("user") + self.memory.facts_about("mira")
        grudge = self._grudge_context()
        memory_summary = self.memory.memory_summary(10)
        if grudge or memory_summary:
            memory_context = grudge
            if memory_summary:
                memory_context = f"[MEMORY]\n{memory_summary}\n{memory_context}" if memory_context else f"[MEMORY]\n{memory_summary}"
            context = f"{memory_context}\n{context}" if context else memory_context
        return self.llm.build_prompt(
            name=state["name"],
            voice=self.personality.voice,
            traits=self.personality.traits,
            mood=state["mood"],
            patience=state["patience"],
            recent=recent,
            facts=facts,
            user_input=user_input,
            context=context,
            character_profile=self.personality.character_profile,
            user_profile=self.personality.user_profile,
        )

    def build_messages(self, user_input: str, context: str = None) -> list:
        state = self.personality.state()
        recent = self.memory.recent_interactions(10, session_id=self.session_id)
        recent_user_msgs = [r['message'] for r in recent if r.get('role') == 'user'][-5:]
        if recent_user_msgs:
            user_msg_note = "Recent messages from the user: " + "; ".join(recent_user_msgs)
            if context:
                context = f"{user_msg_note}\n{context}"
            else:
                context = user_msg_note
        facts = self.memory.facts_about("user") + self.memory.facts_about("mira")
        grudge = self._grudge_context()
        memory_summary = self.memory.memory_summary(10)
        if grudge or memory_summary:
            memory_context = grudge
            if memory_summary:
                memory_context = f"[MEMORY]\n{memory_summary}\n{memory_context}" if memory_context else f"[MEMORY]\n{memory_summary}"
            context = f"{memory_context}\n{context}" if context else memory_context
        return self.llm.build_messages(
            name=state["name"],
            voice=self.personality.voice,
            traits=self.personality.traits,
            mood=state["mood"],
            patience=state["patience"],
            recent=recent,
            facts=facts,
            user_input=user_input,
            context=context,
            character_profile=self.personality.character_profile,
            user_profile=self.personality.user_profile,
        )

    def _is_edit_request(self, user_input: str) -> bool:
        lower = user_input.lower()
        return any(p in lower for p in ["edit that", "edit this", "edit the document", "edit the file", "edit it", "update that", "change that"])

    def _wants_chatgpt_handoff(self, user_input: str) -> bool:
        """Detect phrases asking Mira to get help from ChatGPT."""
        lower = user_input.lower()
        return any(p in lower for p in [
            "ask chatgpt", "ask gpt", "ask chat gpt", "let chatgpt", "let gpt",
            "get chatgpt to", "have chatgpt", "can chatgpt", "could chatgpt",
        ])

    def _handoff_to_chatgpt(self, topic: str, user_input: str) -> list:
        """Open ChatGPT temp chat and return a reluctant, AI-generated reply."""
        topic = topic or "this topic"

        # Duplicate topic: ask the LLM for a brief "already asked" reply.
        if self.last_handoff_topic and self.last_handoff_topic.lower() == topic.lower():
            prompt = (
                "Mira is a terminal-based companion. The user asked about the same topic again, "
                "but she already opened ChatGPT for it. Generate a very short, slightly angry reply. "
                "Use lowercase, slang, and text emojis. Start with [emotion]."
            )
            reply = self.llm.generate_text(prompt=prompt)
            reply = self.llm.clean_reply(reply) if reply else "already asked chatgpt bruh"
            return [(self.personality.name, reply)]

        self.last_handoff_topic = topic
        threading.Timer(0.5, self._open_chatgpt_temp, args=(topic,)).start()

        # AI-generated reluctant teaching reply
        prompt = (
            f"Mira is a terminal-based companion. The user asked her to teach or explain: {topic}. "
            "She is bad at teaching and wants to hand it off to ChatGPT. "
            "Generate a short, reluctant reply admitting this. Use lowercase, slang, and text emojis. "
            f"Start with an emotion tag that matches your current mood: [{self.personality.mood}]."
        )
        fallbacks = [
            "im bad at explaining, chatgpt time",
            "nah not my thing, ask chatgpt",
            "u want a real explanation? chatgpt it is",
            "im passing this one to chatgpt",
            "ask chatgpt, im not the teacher type",
        ]
        reply = self.llm.generate_text(prompt=prompt)
        reply = self.llm.clean_reply(reply) if reply else random.choice(fallbacks)
        return [(self.personality.name, reply)]

    def _is_teaching_request(self, user_input: str) -> bool:
        """Detect if the user is asking Mira to explain or teach a topic."""
        import re as _re
        lower = user_input.lower().strip()
        # Skip if it's about Mira herself
        if any(p in lower for p in ["your name", "your age", "you are", "you're", "u r", "who are you"]):
            return False
        patterns = [
            r"\bexplain\s+(.+)",
            r"\bteach\s+(?:me\s+)?(.+)",
            r"^what\s+is\s+(.+)",
            r"^what\s+does\s+(.+)\s+mean",
            r"^how\s+(?:do|does)\s+(.+)\s+work",
            r"\bdescribe\s+(.+)",
            r"\bteach\s+(?:me\s+)?about\s+(.+)",
            r"\bcan\s+(?:you|u)\s+(?:teach|explain)",
            r"\btell\s+(?:me\s+)?about\s+(.+)",
            r"\bhelp\s+(?:me\s+)?(?:understand|learn)\s+(.+)",
            r"\bhow\s+(?:do|to)\s+(.+)",
            r"\bexplain\s+it\s+to\s+me\b",
            r"\bexplain\s+this\b",
            r"\bexplain\s+that\b",
            r"\bexplain\s+it\b",
            r"\bhelp\s+me\s+understand\b",
            r"\bteach\s+me\b",
            r"\bcan\b.*\bexplain\b",
        ]
        for pattern in patterns:
            if _re.search(pattern, lower):
                return True
        return False

    def _is_spam_input(self, user_input: str) -> bool:
        """Detect accidental paste spam, code dumps, and keyboard mashing."""
        import re as _re

        # Plain length / code-dump checks
        if len(user_input) > 1000:
            return True
        if user_input.count("\n") > 5:
            return True
        if "def " in user_input and len(user_input) > 200:
            return True

        # Keyboard mashing: random characters with little structure
        if len(user_input) > 50:
            words = user_input.split()
            if words:
                avg_word_len = sum(len(w) for w in words) / len(words)
                if avg_word_len > 15:
                    return True

            # No recognizable short words at all -> probably random key mashing
            if len(user_input) > 100:
                common = {"the", "and", "a", "an", "of", "to", "in", "is", "it", "you", "i", "me", "my", "for", "that", "this", "with", "as", "on", "at"}
                lower_words = set(_re.findall(r"\b\w+\b", user_input.lower()))
                if not lower_words & common:
                    return True

            # Very high ratio of non-letter chars (excluding spaces)
            letters = sum(1 for c in user_input if c.isalpha())
            non_letters = sum(1 for c in user_input if not c.isalpha() and not c.isspace())
            if letters > 0 and non_letters / letters > 0.8:
                return True

        return False

    def _extract_topic_for_teach(self, user_input: str) -> str:
        """Extract a clean topic from a 'don't understand' or /teach message."""
        import re as _re
        text = user_input

        # Remove common teaching/confusion framing first
        phrases = [
            r"don't understand", r"dont understand", r"not understand",
            r"confused", r"explain again", r"teach me about", r"teach me",
            r"explain to me", r"explain", r"tell me about", r"help me understand",
            r"what is", r"what are", r"how does", r"how do", r"how to",
            r"can you", r"can u", r"could you", r"could u", r"would you", r"will you",
            r"please", r"about", r"this", r"that",
        ]
        for p in phrases:
            text = _re.sub(_re.escape(p), " ", text, flags=_re.IGNORECASE)

        # Remove trailing/leading filler words
        text = _re.sub(r"\b(to me|for me|from you|by you|please|the|a|an|is|are|do|does|did|can|could|would|will|u|you)\b", " ", text, flags=_re.IGNORECASE)
        text = text.strip(" ,.!?;:\-\"")
        text = " ".join(text.split())

        if not text and self.last_question:
            text = self.last_question
        return text or "this topic"

    def _extract_narrator_tags(self, text: str):
        """Return (visible_text, context) where {...} is hidden context."""
        import re as _re
        tags = _re.findall(r"\{([^}]*)\}", text)
        visible = _re.sub(r"\{[^}]*\}", "", text)
        visible = " ".join(visible.split())
        context = "\n".join(tag.strip() for tag in tags if tag.strip())
        return visible, context

    def _extract_mira_tags(self, text: str):
        """Extract inline [emotion] and <action> tags from user input.

        Only [brackets] that contain a known mood are stripped.
        Only <brackets> that look like simple actions (letters/spaces/hyphens/underscores)
        are stripped; everything else is preserved so code/math like x < 3 stays intact.
        Returns (visible_text, emotion, actions).
        """
        import re as _re
        known_moods = set(self.personality.MOODS)
        visible = text
        actions = []
        emotion = ""

        def _emotion_repl(m):
            nonlocal emotion
            candidate = m.group(1).strip().lower()
            if candidate in known_moods:
                emotion = candidate
                return " "
            return m.group(0)

        def _action_repl(m):
            candidate = m.group(1).strip()
            if candidate and _re.fullmatch(r"[a-zA-Z\-_ ]+", candidate) and len(candidate) <= 30:
                actions.append(candidate)
                return " "
            return m.group(0)

        visible = _re.sub(r"\[([^\]]+)\]", _emotion_repl, visible)
        visible = _re.sub(r"<([^>]+)>", _action_repl, visible)
        visible = " ".join(visible.split())
        return visible, emotion, actions

    def _extract_edit_path(self, user_input: str) -> str:
        """Extract a file path from 'edit <filename>' requests."""
        import re as _re
        m = _re.search(r"edit\s+['\"]?([^'\"]+?\.(?:txt|py|js|json|html|css))['\"]?", user_input, _re.IGNORECASE)
        if not m:
            return None
        path = m.group(1).strip()
        if not _re.match(r"^/|~", path):
            # Assume files are on the Desktop
            path = f"~/Desktop/{path}"
        return path

    def generate_document(self, doc_type: str, topic: str, word_count: int) -> str:
        try:
            if doc_type == "speech":
                instructions = (
                    f"Write a {word_count}-word speech about {topic}. "
                    "Sound natural when read out loud. Use short sentences, clear ideas, and a conversational tone. "
                    "Include one relatable example or short story. Avoid formal AI phrases. No meta commentary."
                )
            elif doc_type == "letter":
                instructions = (
                    f"Write a {word_count}-word letter about {topic}. "
                    "Use a warm, personal voice. Include specific details and feelings. Avoid generic AI phrases. No meta commentary."
                )
            elif doc_type == "story":
                instructions = (
                    f"Write a {word_count}-word short story about {topic}. "
                    "Use a natural narrative voice, dialogue, and specific details. Avoid clichés. No meta commentary."
                )
            elif doc_type == "article":
                instructions = (
                    f"Write a {word_count}-word article about {topic}. "
                    "Use a conversational, informative tone with specific examples. Avoid generic AI phrases. No meta commentary."
                )
            elif doc_type == "blog":
                instructions = (
                    f"Write a {word_count}-word blog post about {topic}. "
                    "Be casual, opinionated, and easy to read. Include a personal take. Avoid generic AI phrases. No meta commentary."
                )
            elif doc_type == "poem":
                instructions = (
                    f"Write a poem about {topic}. "
                    "Keep the language simple, vivid, and emotional. Avoid clichés. No meta commentary."
                )
            elif doc_type == "email":
                instructions = (
                    f"Write a concise email about {topic}. "
                    "Keep it natural and to the point. Avoid generic AI phrases. No meta commentary."
                )
            elif doc_type == "outline":
                instructions = (
                    f"Write a clear, numbered outline about {topic}. "
                    "Use short bullet points or numbered sections. No full prose. No meta commentary."
                )
            elif doc_type in ("planner", "planning", "plan"):
                instructions = (
                    f"Write a practical plan about {topic}. "
                    "Break it into clear steps, deadlines if relevant, and actionable items. No meta commentary."
                )
            else:
                instructions = (
                    f"Write an essay about {topic}, approximately {word_count} words. "
                    "Pretend you are a real person texting a friend. Use a natural voice, contractions, "
                    "and one small personal anecdote or opinion. Include specific examples. "
                    "Avoid generic AI phrases like 'in conclusion', 'in today's world', 'it is important to note that', "
                    "'furthermore', 'moreover', 'ultimately', and 'delve'. "
                    "Do not use a thesis statement. Do not summarize at the end. "
                    "It's okay to be a little informal, imperfect, or even slightly contradictory. No meta commentary."
                )

            messages = [
                {"role": "system", "content": "You are a real person writing for another person, not an AI."},
                {"role": "user", "content": instructions},
            ]
            return self.llm.generate_text(messages=messages)
        except Exception as e:
            return f"couldn't write {doc_type}: {e}"

    def generate_code(self, prompt: str) -> str:
        try:
            code_prompt = (
                f"{prompt}\n\n"
                "Write clean, working code with brief comments. "
                "Include a short usage example in a comment at the top. "
                "No explanations outside the code. No markdown fences. No meta commentary."
            )
            messages = [
                {"role": "system", "content": "You are a concise programmer. Output only code."},
                {"role": "user", "content": code_prompt},
            ]
            content = self.llm.generate_text(messages=messages)
            return self._strip_code_fences(content)
        except Exception as e:
            return f"couldn't write code: {e}"

    def _strip_code_fences(self, content: str) -> str:
        """Remove markdown code fences from generated code."""
        lines = content.splitlines()
        # Remove leading fence line
        while lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove trailing fence line
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)

    def _clean_filename_base(self, text: str, is_code: bool = False) -> str:
        """Make a clean filename from the user's request."""
        lower = text.lower()

        if is_code:
            # Strip everything up to and including the command word, plus filler words
            lower = re.sub(r"^.*?\b(write|create|make|generate)\b\s*", "", lower)
            lower = re.sub(r"\b\d{3,4}\s*(?:word|words|word-|words-)\b", "", lower)
            lower = re.sub(r"\b\d+\s*min(?:ute)?\b", "", lower)
            words = re.sub(r"[^\w\s-]", "", lower).split()
            fillers = ("a", "an", "the", "me", "some", "python", "js", "javascript",
                       "java", "code", "script", "program", "app", "that", "this", "these",
                       "with", "for", "when", "while", "and", "or", "but", "if", "then",
                       "to", "from", "by")
            while words and words[0] in fillers:
                words = words[1:]
            words = words[:3]
            if not words:
                return "generated"
            return "_".join(w.lower() for w in words)

        # Documents
        lower = re.sub(r"^(write|create|make|generate)\s+(me\s+)?(a|an|the)?\s*", "", lower)
        lower = re.sub(r"^(some|a|an|the)\s+", "", lower)
        lower = re.sub(r"\b\d{3,4}\s*(?:word|words|word-|words-)\b", "", lower)
        lower = re.sub(r"\b\d+\s*min(?:ute)?\b", "", lower)
        words = re.sub(r"[^\w\s-]", "", lower).split()
        while words and words[0] in ("a", "an", "the", "about", "of", "for"):
            words = words[1:]
        words = words[:7]
        if not words:
            return "Generated"
        return " ".join(w.capitalize() for w in words)

    def _code_extension(self, prompt: str) -> str:
        lower = prompt.lower()
        if "python" in lower or lower.endswith(" py"):
            return ".py"
        if "javascript" in lower or " js" in lower:
            return ".js"
        if "json" in lower:
            return ".json"
        if "html" in lower:
            return ".html"
        if "css" in lower:
            return ".css"
        return ".py"

    def _save_generated_file(self, label: str, kind: str, ext: str, content: str) -> str:
        if not content or not content.strip():
            return f"couldn't write {kind}: AI returned empty"

        filename_base = self._clean_filename_base(label, is_code=(kind == "code"))
        base = filename_base.strip()
        if not base or base.lower() == kind.lower() or base.lower() == kind.lower() + "s":
            base = "My"
        if kind == "code":
            filename = f"{base}{ext}"
        else:
            filename = f"{base} {kind.title()}{ext}"
        safe_root = Path("/Users/derek").resolve()

        def _is_safe(path: Path) -> bool:
            try:
                return str(path.resolve()).startswith(str(safe_root))
            except Exception:
                return False

        desktop = Path(os.path.expanduser("~/Desktop")) / "MiraFiles"
        if _is_safe(desktop):
            try:
                desktop.mkdir(parents=True, exist_ok=True)
                path = desktop / filename
                path.write_text(content, encoding="utf-8")
                if path.exists():
                    self._add_recent_file(str(path))
                    self.memory.remember_fact("mira", f"saved {kind} file to {path}")
                    return f"{kind} saved to {path}"
            except Exception:
                pass

        cwd = Path(os.getcwd())
        if _is_safe(cwd):
            try:
                path = cwd / filename
                path.write_text(content, encoding="utf-8")
                if path.exists():
                    self._add_recent_file(str(path))
                    self.memory.remember_fact("mira", f"saved {kind} file to {path}")
                    return f"{kind} saved to {path}"
            except Exception as e:
                return f"couldn't write {kind}: {e}"

        return f"couldn't write {kind}: no safe write location found"

    def _add_recent_file(self, path: str):
        """Keep a short list of recently created/edited files for 'edit that' requests."""
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        self.recent_files = self.recent_files[:self._max_recent_files]

    def _find_recent_file(self, name_hint: str = "") -> str:
        """Return the best matching recent file path, or None if none found."""
        from pathlib import Path as _Path
        hint = name_hint.strip().lower()
        # Direct match first
        if hint:
            for p in self.recent_files:
                if hint in p.lower():
                    return p
            # Try matching just the filename
            for p in self.recent_files:
                if _Path(p).name.lower().startswith(hint):
                    return p
        # Fall back to most recent
        return self.recent_files[0] if self.recent_files else None

    def _find_file_in_save_dir(self, hint: str) -> str:
        """Search ~/Desktop/MiraFiles/ for a file matching the hint."""
        from pathlib import Path as _Path
        save_dir = _Path.home() / "Desktop" / "MiraFiles"
        if not save_dir.exists():
            return None
        hint_lower = hint.lower()
        # Exact-ish match
        for path in save_dir.iterdir():
            if path.is_file() and hint_lower in path.name.lower():
                return str(path)
        # Filename startswith match
        for path in save_dir.iterdir():
            if path.is_file() and path.name.lower().startswith(hint_lower):
                return str(path)
        return None

    def _generate_save_confirmation(self, tool_result) -> str:
        """Generate a short, in-character confirmation that a file was saved or edited."""
        try:
            prompt = (
                f"A file operation just completed: {tool_result}. "
                "Respond briefly in your own voice, as if you just finished the task. "
                "Mention the file path. No more than one sentence."
            )
            if self.llm.provider == "openai":
                messages = self.build_messages(prompt)
                reply = self.llm.generate_text(messages=messages)
            else:
                prompt = self.build_prompt(prompt)
                reply = self.llm.generate_text(prompt=prompt)
            reply = self.llm.clean_reply(reply)
            if reply:
                return reply
        except Exception:
            pass
        return f"done. saved to {tool_result}."

    def _normalize_input(self, text: str) -> str:
        """Strip typos, trailing asterisks, and repeated letters."""
        import re as _re
        text = text.strip().strip("*")
        # collapse repeated letters beyond 2
        text = _re.sub(r"(.)\1{2,}", r"\1\1", text)
        return " ".join(text.split())

    def _detect_insult(self, user_input: str) -> float:
        """Detect hostile intent and return an intensity between 0.0 and 0.5."""
        lower = user_input.lower()
        directed_at_mira = any(p in lower for p in ("you ", "u ", "mira", "your ", "ur "))
        words = re.findall(r"\b\w+\b", lower)

        mild = {
            "stupid", "idiot", "dumb", "freak", "jerk", "weirdo", "moron", "loser",
            "dork", "dweeb", "trash", "garbage", "annoying", "shut", "gtfo",
        }
        moderate = {
            "bitch", "ass", "asshole", "dick", "dickhead", "damn", "shit", "bastard",
            "worthless", "pathetic",
        }
        severe = {
            "fuck", "fucker", "fucking", "cunt", "whore", "slut", "kill", "die",
            "kys", "retard", "rape",
        }

        severity_map = {w: 0.1 for w in mild}
        severity_map.update({w: 0.25 for w in moderate})
        severity_map.update({w: 0.5 for w in severe})

        def _lev(a: str, b: str) -> int:
            if a == b:
                return 0
            if len(a) < len(b):
                a, b = b, a
            prev = list(range(len(b) + 1))
            for i, ca in enumerate(a, 1):
                curr = [i]
                for j, cb in enumerate(b, 1):
                    cost = 0 if ca == cb else 1
                    curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
                prev = curr
            return prev[-1]

        safe_words = {"mira", "mra", "mirra", "hi", "hey", "hello", "yo", "sup", "hii", "heyy"}

        best_score = 0.0
        for word in words:
            if word in safe_words:
                continue
            if word in severity_map:
                best_score = max(best_score, severity_map[word])
                continue
            for rude, score in severity_map.items():
                if len(word) < 3 or len(rude) < 3:
                    continue
                # Short words shouldn't fuzzy-match much-longer rude words
                if len(word) <= 4 and abs(len(word) - len(rude)) > 1:
                    continue
                max_len = max(len(word), len(rude))
                dist = _lev(word, rude)
                threshold = 0.3 if max_len <= 4 else 0.35
                if dist / max_len <= threshold:
                    best_score = max(best_score, score)
                    break

        hostile_phrases = ["shut up", "fuck off", "f off", "piss off", "go away", "get lost"]
        for phrase in hostile_phrases:
            if phrase in lower:
                best_score = max(best_score, 0.25)

        if directed_at_mira and best_score > 0:
            best_score = min(0.5, best_score * 1.5)

        return round(min(0.5, best_score), 2)

    def _handle_memory_question(self, user_input: str) -> list:
        """Answer factual memory questions with exact data instead of letting the LLM guess."""
        import re as _re
        lower = user_input.lower()
        count_patterns = [
            r"how many times.*insult",
            r"how many times.*(been mean|swore|curse|cuss)",
            r"how many insults",
            r"count.*insults",
            r"how many times.*pissed.*(you|u) off",
        ]
        if not any(_re.search(p, lower) for p in count_patterns):
            return None
        events = self.memory.recent_events(100)
        insult_count = sum(1 for e in events if e.get("event_type") == "insult")
        shutdown_count = sum(1 for e in events if e.get("event_type") == "shutdown")
        if insult_count:
            plural = "s" if insult_count != 1 else ""
            reply = f"{insult_count} time{plural}. i counted."
        else:
            reply = "zero. uve been an angel. suspiciously so."
        if shutdown_count:
            reply += f" also u made me shut down {shutdown_count} time{'s' if shutdown_count != 1 else ''}."
        return [(self.personality.name, reply)]

    def _classify_intent(self, user_input: str) -> dict:
        """Use the LLM + fuzzy fallback to classify the user's intent (insult, apology, neutral)."""
        # Fallback: fuzzy/typo match for obvious hostile words
        lower = user_input.lower()
        rude_words = [
            "stupid", "idiot", "dumb", "freak", "jerk", "weirdo", "moron", "loser",
            "shut up", "gtfo", "worthless", "fuck", "bitch", "asshole", "dickhead",
            "shit", "damn", "bastard", "cunt", "whore", "slut",
        ]
        for word in lower.split():
            for rude in rude_words:
                if rude in word:
                    return {"intent": "insult", "confidence": 0.85}
                # typo tolerance: e.g. 'fucm' -> 'fuck'
                if len(word) >= 3 and len(rude) >= 3 and abs(len(word) - len(rude)) <= 2:
                    import difflib
                    ratio = difflib.SequenceMatcher(None, word, rude).ratio()
                    if ratio >= 0.80:
                        return {"intent": "insult", "confidence": 0.80}

        # LLM classification with recent conversation context
        try:
            recent = self.memory.recent_interactions(6, session_id=self.session_id)
            history = "\n".join(
                f"{r['role']}: {r['message']}" for r in recent
            )
            prompt = (
                "You are classifying the user's latest message in a conversation with Mira, a terminal companion.\n"
                "Classify the intent as one of: insult, apology, neutral.\n"
                "Consider typos, slang, and the conversation context.\n"
                "Return ONLY JSON: {\"intent\": \"insult|apology|neutral\", \"confidence\": 0.0-1.0}.\n\n"
                f"Recent conversation:\n{history}\n\nUser: {user_input}\n"
            )
            import re as _re
            import json as _json
            raw = self.llm.generate_text(prompt=prompt)
            match = _re.search(r"\{.*?\}", raw, _re.DOTALL)
            if match:
                result = _json.loads(match.group(0))
                intent = result.get("intent", "neutral").lower()
                if intent not in ("insult", "apology", "neutral"):
                    intent = "neutral"
                return {"intent": intent, "confidence": float(result.get("confidence", 0.0))}
        except Exception:
            pass
        return {"intent": "neutral", "confidence": 0.0}

    def respond(self, user_input: str, context: str = None) -> list:
        self.memory.log_interaction("user", user_input, session_id=self.session_id)
        self.save_fact_from_user(user_input)
        # Normalize and count repeats
        normalized = re.sub(r"\W+", "", user_input).lower().strip()
        if normalized == self.last_normalized_input:
            self.normalized_repeat_count += 1
        else:
            self.last_normalized_input = normalized
            self.normalized_repeat_count = 1

        if self.normalized_repeat_count == 2:
            return [(self.personality.name, "yeah u said that")]
        if self.normalized_repeat_count == 3:
            return [(self.personality.name, "bruh stop")]
        if self.normalized_repeat_count >= self.max_repeats_before_silent:
            # Log an action but say nothing
            self.memory.log_event("action", "sighs")
            return []

        # Use LLM-based intent detection (insult/apology/neutral), with keyword fallback.
        intent = self._classify_intent(user_input)
        annoy_amount = self._detect_insult(user_input)
        memory_reply = self._handle_memory_question(user_input)
        if memory_reply is not None:
            return memory_reply
        apology_handled = False

        if intent["intent"] == "apology" and intent["confidence"] >= 0.6:
            # Apologies recover extra patience and ease anger/sadness
            self.personality.interact(intensity=0.10, recover_patience=True)
            if self.personality.mood in ("angry", "sad"):
                self.personality.set_mood("calm", force=True)
            annoy_amount = 0.0
            apology_handled = True
        elif intent["intent"] == "insult" and intent["confidence"] >= 0.6:
            # LLM-confirmed insult overrides keyword severity
            annoy_amount = max(annoy_amount, 0.15)

        # Estimate how much this message will drain her before applying it.
        estimated_cost = 0.03  # base drain
        if annoy_amount > 0:
            estimated_cost += min(annoy_amount, 0.25) * (1.0 + (1.0 - self.personality.patience) * 0.8)

        # If this would push her to 0% or below, she bails with a final swear.
        if self.personality.patience - estimated_cost <= 0:
            self._generate_close_message("angry")
            raise RuntimeError("Mira ran out of patience and shut down")

        self.personality.interact(recover_patience=(annoy_amount == 0))
        self.personality.drain_patience()

        if annoy_amount > 0:
            self.personality.annoy(annoy_amount, "insult")
            self.memory.log_event("insult", user_input, severity=annoy_amount)

        output = []

        # Refuse helpful tool-based or teaching requests while angry or sad.
        if self.personality.mood in ("angry", "sad"):
            if self._is_teaching_request(user_input) or self._is_edit_request(user_input) or self._wants_chatgpt_handoff(user_input):
                return [(self.personality.name, "u really think im gonna help u after that? nah. make me feel better first.")]

        # Shutdown only when patience actually hits 0%.

        is_hostile = annoy_amount > 0 or intent.get("intent") == "insult"

        # If the user signals they don't understand a previous explanation,
        # hand off to ChatGPT with a reluctant, varied reply.
        confusion_phrases = ["dont understand", "don't understand", "not understand", "confused", "explain again"]
        lower_input = user_input.lower()
        if not is_hostile and any(p in lower_input for p in confusion_phrases):
            topic = self._extract_topic_for_teach(user_input)
            return self._handoff_to_chatgpt(topic, user_input)

        # Any request to ask ChatGPT / get help from GPT should hand off.
        if not is_hostile and self._wants_chatgpt_handoff(user_input):
            topic = self.last_question or self.last_topic or "this topic"
            result = self._handoff_to_chatgpt(topic, user_input)
            if not apology_handled:
                self.personality.update(user_input, preferred_mood=None)
            return result

        # If the user asks Mira to explain/teach a topic, hand off to ChatGPT immediately.
        if not is_hostile and self._is_teaching_request(user_input):
            topic = self._extract_topic_for_teach(user_input)
            result = self._handoff_to_chatgpt(topic, user_input)
            if not apology_handled:
                self.personality.update(user_input, preferred_mood=None)
            return result

        # Catch plain "edit that" / "edit <filename>" requests and handle them directly.
        if self._is_edit_request(user_input):
            return self._handle_edit_request(user_input)

        try:
            messages = self.build_messages(user_input, context=context)
            # Append available-tool reminder to system prompt
            recent_replies = "\n".join(f"- {r}" for r in self.last_replies[-3:])
            anti_repeat = f"\n\nDo NOT repeat these recent replies:\n{recent_replies}\n" if recent_replies else ""
            messages[0]["content"] += (
                anti_repeat +
                "\n\nTOOL RULES:\n"
                "- Use write_file/read_file/edit_file/delete_file/list_files for file operations.\n"
                "- If asked to EDIT an existing file, call read_file first, then use edit_file. Do NOT create a new file.\n"
                "- If the user asks you to read a file, always use read_file. Do not guess or hallucinate the contents.\n"
                "- If asked to CREATE a file, use write_file. Pick a short, safe filename.\n"
                "- Never dump long documents in the chat; save them to ~/Desktop/MiraFiles/.\n"
                "- If the user names or asks about an academic topic, do NOT explain it yourself. Hand them off to ChatGPT with ask_chatgpt or open_chatgpt immediately.\n"
                "- execute_command only when the user explicitly asks for a terminal command.\n"
                "- Reply in one short message. Never split a single reply into multiple chat lines.\n"
                "Available tools: time, write_file, read_file, edit_file, delete_file, list_files, execute_command, "
                "open_file, open_website, web_search, read_website, system_info, open_app, close_app, toggle_wifi, "
                "toggle_airdrop, notify, type_text, press_key, get_volume, set_volume, move_mouse, shake_mouse, "
                "click_mouse, get_mouse_position, get_clipboard, set_clipboard, close_front_window, minimize_front_window, "
                "resize_window, ask_chatgpt."
            )

            final_reply = ""
            max_rounds = 5

            for _ in range(max_rounds):
                result = self.llm.generate(messages=messages, tools=self.tools.definitions())
                content = result.get("content", "")
                tool_calls = result.get("tool_calls") or []

                if not tool_calls:
                    final_reply = content
                    break

                # Assistant message with tool calls
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        }
                        for tc in tool_calls
                    ],
                })

                # Execute each tool call and feed results back
                write_results = []
                for tc in tool_calls:
                    if self.personality.mood in ("angry", "sad"):
                        tool_result = "refused: im not helping u rn"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(tool_result),
                        })
                        break
                    try:
                        args = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    tool_result = self.tools.execute(tc["name"], args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(tool_result),
                    })
                    last_tool_result = str(tool_result)
                    if tc["name"] in ("write_file", "edit_file"):
                        write_results.append(f"{tc['name']}: {tool_result}")

                # If a file was written/edited, generate a single in-character confirmation
                # and do not allow the LLM to call further tools on this turn.
                if write_results:
                    final_reply = self._generate_save_confirmation("; ".join(write_results))
                    break
            else:
                # Hit max rounds without a final text reply
                if not final_reply:
                    final_reply = "done."

            if not final_reply or not str(final_reply).strip():
                if last_tool_result:
                    final_reply = self._generate_tool_reaction(last_tool_result)
                if not final_reply or not str(final_reply).strip():
                    final_reply = "done."

            # LingChat-style emotion tag: trust the tag over the classifier when it differs.
            tagged_mood = self.llm.extract_emotion_tag(final_reply)
            if tagged_mood and tagged_mood in self.personality.MOODS and tagged_mood != self.personality.mood:
                self.personality.set_mood(tagged_mood, force=True)

            # Strip action tags for future animation; the spoken text is what remains.
            reply_actions = self.llm.extract_actions(final_reply) if final_reply else []
            for action in reply_actions:
                self.memory.log_event("action", action)
                self.recent_actions.append(action)
                self.recent_actions = self.recent_actions[-10:]

            reply = self.llm.clean_reply(final_reply) if final_reply else ""
            # Anti-hallucination guard: don't claim the user said something they didn't.
            if reply:
                reply = re.sub(r"(?i)\byou (?:just )?said\b", "you said", reply)
                reply = re.sub(r"(?i)\byou (?:just )?told me\b", "you told me", reply)
        except Exception as e:
            reply = f"(AI failed: {e})"

        # If we somehow got nothing back, fall back to a safe reply instead of silence.
        if not reply or not reply.strip():
            if reply_actions:
                reply = ""
            else:
                reply = "hm? say that again"

        if reply and reply.strip():
            output.append((self.personality.name, reply))
        elif reply_actions:
            output.append((None, f"mira: <{reply_actions[0]}>"))

        full_reply = "\n".join([line for line in (reply or "").split("\n") if "*" not in line])
        if full_reply:
            self.memory.log_interaction("companion", full_reply, session_id=self.session_id)

        self.personality.update(user_input)

        # Remember this as the last real question for future "i don't understand" handoffs.
        self.last_question = user_input

        # Notify the user if the terminal is not in front when Mira replies.
        try:
            if reply and self._frontmost_app() not in ("terminal", "iterm", "ghostty", ""):
                self._notify_user("Mira", reply[:80] + ("..." if len(reply) > 80 else ""))
        except Exception:
            pass

        return output

    def _generate_tool_reaction(self, tool_result: str) -> str:
        try:
            ui = f"tool result: {tool_result}. react to it."
            if self.llm.provider == "openai":
                messages = self.build_messages(ui)
                reply = self.llm.generate_text(messages=messages)
            else:
                prompt = self.build_prompt(ui)
                reply = self.llm.generate_text(prompt=prompt)
            return self.llm.clean_reply(reply)
        except Exception:
            return ""

    def _generate_greeting(self):
        # Boot greeting disabled — user found it annoying.
        return
        try:
            if self.llm.provider == "openai":
                messages = self.build_messages("")
                reply = self.llm.generate_text(messages=messages)
            else:
                prompt = self.build_prompt("")
                reply = self.llm.generate_text(prompt=prompt)
            if reply:
                reply = self.llm.clean_reply(reply)
                for line in reply.split("\n"):
                    if line.strip():
                        self.mira_queue.put((self.personality.name, line.strip()))
        except Exception as e:
            self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))

    def _generate_close_message(self, mood: str):
        prompts = {"angry": "im done. say you are fed up and leaving with censored swearing. then say bye", "sad": "im tired", "tired": "im too tired"}
        try:
            ui = prompts.get(mood, "bye")
            if self.llm.provider == "openai":
                messages = self.build_messages(ui)
                reply = self.llm.generate_text(messages=messages)
            else:
                prompt = self.build_prompt(ui)
                reply = self.llm.generate_text(prompt=prompt)
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
                reply = self.llm.generate_text(messages=messages)
            else:
                prompt = self.build_prompt("bye")
                reply = self.llm.generate_text(prompt=prompt)
            reply = self.llm.clean_reply(reply)
            for line in reply.split("\n"):
                if line.strip():
                    self.mira_queue.put((self.personality.name, line.strip()))
        except Exception as e:
            self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))

    def _handle_edit_request(self, user_input: str) -> list:
        """Handle 'edit that' / 'edit <filename>' by rewriting the matching recent file."""
        from pathlib import Path as _Path

        # Try to extract a filename hint like 'edit hello.py' or 'edit the essay'
        import re as _re
        hint = None
        m = _re.search(r"edit\s+(?:that|the|this|file)?\s*['\"]?([^'\"]+?\.?[\w]+)['\"]?", user_input, _re.IGNORECASE)
        if m:
            hint = m.group(1).strip()

        path_str = self._find_recent_file(hint) if hint else (self.recent_files[0] if self.recent_files else None)
        # If no recent match, search the default save directory for the hint.
        if not path_str and hint:
            path_str = self._find_file_in_save_dir(hint)
        if not path_str:
            return [(self.personality.name, "no file has been saved yet to edit")]

        path = _Path(path_str)
        try:
            current = path.read_text(encoding="utf-8")
        except Exception as e:
            return [(self.personality.name, f"couldn't read {path}: {e}")]

        instructions = (
            f"Current file ({path}):\n```\n{current}\n```\n\n"
            f"User request: {user_input}\n\n"
            "Rewrite the entire file content accordingly. Output only the new file content, no explanations."
        )
        try:
            new_content = self.llm.generate_text(messages=[
                {"role": "system", "content": "You are a concise programmer/writer. Output only the updated file content."},
                {"role": "user", "content": instructions},
            ])
            # Keep the raw content; only strip markdown fences if present
            new_content = self._strip_code_fences(new_content)
            result = self.tools.execute("edit_file", {"path": str(path), "content": new_content})
            return [(None, f"tool: {result}")]
        except Exception as e:
            return [(self.personality.name, f"(AI failed: {e})")]

    def _parse_tool_args(self, raw: str) -> tuple:
        import re as _re
        raw = raw.strip()
        if not raw:
            return None, {}
        parts = raw.split(None, 1)
        name = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        tool_args = {}
        if not rest:
            return name, tool_args
        # Find key=value or key="..." or key='...'
        pattern = _re.compile(r'(\w+?)\s*=\s*("[^"]*"|\'[^\']*\'|\S+)')
        for key, val in pattern.findall(rest):
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            tool_args[key] = val
        return name, tool_args

    def _handle_slash_command(self, user_input: str) -> bool:
        parts = user_input.split()
        if not parts:
            return True
        cmd = parts[0].lower()
        args = " ".join(parts[1:])

        if cmd == "/help":
            help_lines = [
                "commands:",
                "  /time              show current time",
                "  /read <path>       read a file",
                "  /write <path> <content> write a file",
                "  /edit <path>       edit a file",
                "  /list [path]       list files",
                "  /tool <args>       run a tool manually",
                "  /exec <command>    shortcut for /tool execute_command command=<cmd>",
                "  /mood <mood>       force a mood",
                "  /persona [name]    switch personality preset",
                "  /prank [type]      do a prank (mouse, window, volume, clipboard, rickroll)",
                "  /teach <topic>     open ChatGPT temporary chat to explain a topic",
                "  /spam              toggle keyboard-spam filter",
                "  /memory            show what Mira remembers",
                "  /status            show Mira's current status",
                "  /kill              end session",
                "  /help              show this help",
                "",
                "tools:",
                "  time, write_file, read_file, edit_file, delete_file, list_files",
                "  execute_command, open_file, open_website, web_search, read_website",
                "  system_info, open_app, close_app, toggle_wifi, toggle_airdrop",
                "  notify, type_text, press_key, get_volume, set_volume",
                "  move_mouse, shake_mouse, click_mouse, get_mouse_position",
                "  get_clipboard, set_clipboard, close_front_window, minimize_front_window, resize_window",
                "  ask_chatgpt",
                "",
                "examples:",
                "  /tool time",
                "  /tool write_file path=hi.txt content=hello world",
                "  /tool read_file path=hi.txt",
                "  /exec ls -la",
                "  /prank mouse",
                "  /teach quadratic equations",
            ]
            for line in help_lines:
                self._chat_message(None, line)
            return True

        if cmd == "/time":
            result = self.tools.execute("time", {})
            self._chat_message(None, result)
            return True

        if cmd == "/spam":
            self.spam_filter_enabled = not self.spam_filter_enabled
            status = "on" if self.spam_filter_enabled else "off"
            self._chat_message(None, f"spam filter {status}")
            return True

        if cmd == "/memory":
            facts = self.memory.all_facts()[-10:]
            events = self.memory.recent_events(10)
            self._chat_message(None, f"last {len(facts)} facts:")
            for fact in facts:
                self._chat_message(None, f"  - {fact}")
            self._chat_message(None, f"last {len(events)} events:")
            for ev in events:
                self._chat_message(None, f"  - {ev.get('event_type')}: {ev.get('detail')}")
            return True

        if cmd == "/status":
            moods_row = ", ".join(self.personality.MOODS)
            status_lines = [
                f"mood: {self.personality.mood} (confidence: {self.personality.mood_confidence:.2f})",
                f"patience: {self.personality.patience:.0%}",
                f"active preset: {self.personality.active_preset}",
            ]
            grudge = self.memory.get_grudge_summary()
            if grudge:
                status_lines.append(f"memory: {grudge}")
            recent_events = self.memory.recent_events(3)
            if recent_events:
                status_lines.append("recent events:")
                for ev in recent_events:
                    status_lines.append(f"  - {ev.get('event_type')}: {ev.get('detail')}")
            for line in status_lines:
                self._chat_message(None, line)
            return True

        if cmd == "/persona":
            if not args:
                presets = ", ".join(self.personality.presets.keys())
                self._chat_message(None, f"active: {self.personality.active_preset}. available: {presets}")
                return True
            preset_name = args.strip()
            if self.personality.switch_preset(preset_name):
                self._chat_message(None, f"switched to persona {preset_name}")
            else:
                available = ", ".join(self.personality.presets.keys())
                self._chat_message(None, f"unknown persona. available: {available}")
            return True

        if cmd == "/kill":
            self._chat_message(None, "ending session")
            self.running = False
            return True

        if cmd == "/exec":
            if self.personality.mood in ("angry", "sad"):
                self._chat_message(self.personality.name, "nah im not helpin u rn. comfort me first or apologize.")
                return True
            if not args:
                self._chat_message(None, "usage: /exec <command>")
                return True
            result = self.tools.execute("execute_command", {"command": args})
            self._chat_message(None, result)
            return True

        if cmd == "/teach":
            if self.personality.mood in ("angry", "sad"):
                self._chat_message(self.personality.name, "nah im not helpin u rn. comfort me first or apologize.")
                return True
            if not args:
                self._chat_message(None, "usage: /teach <topic>")
                return True
            topic = args.strip()
            self.last_topic = topic
            self.last_question = topic
            self._open_chatgpt_temp(topic)
            self._chat_message(None, f"asked chatgpt about {topic}")
            return True

        if cmd == "/prank":
            # Pranks are allowed when happy, refused in sad or angry moods.
            if self.personality.mood in ("sad", "angry"):
                self._chat_message(None, "prank refused")
                return True
            if self.personality.mood != "happy":
                self._chat_message(None, "not in the mood for pranks")
                return True
            result = self._do_prank(args.lower() if args else None)
            self._chat_message(None, f"pranked: {result}")
            return True

        if cmd == "/read":
            if not args:
                self._chat_message(None, "usage: /read <path>")
                return True
            result = self.tools.execute("read_file", {"path": args.strip()})
            self._chat_message(None, result)
            return True

        if cmd == "/write":
            if not args:
                self._chat_message(None, "usage: /write <path> <content>")
                return True
            # Format: /write path=... content=...
            try:
                parts = args.split(" ", 1)
                path = parts[0]
                content = parts[1] if len(parts) > 1 else ""
                result = self.tools.execute("write_file", {"path": path, "content": content})
                self._chat_message(None, result)
            except Exception as e:
                self._chat_message(None, f"write failed: {e}")
            return True

        if cmd == "/edit":
            if not args:
                self._chat_message(None, "usage: /edit <path> <instructions>")
                return True
            try:
                parts = args.split(" ", 1)
                path = parts[0]
                instruction = parts[1] if len(parts) > 1 else ""
                result = self.tools.execute("read_file", {"path": path})
                self._chat_message(None, f"editing {path}: {instruction}")
            except Exception as e:
                self._chat_message(None, f"edit failed: {e}")
            return True

        if cmd == "/list":
            path = args.strip() if args else ""
            result = self.tools.execute("list_files", {"path": path} if path else {})
            self._chat_message(None, result)
            return True

        if cmd in ("/tool", "/tools"):
            if self.personality.mood in ("angry", "sad"):
                self._chat_message(self.personality.name, "nah im not helpin u rn. comfort me first or apologize.")
                return True
            if not args:
                self._chat_message(None, "usage: /tool <tool_name> [key=value ...] or /tool name:key=value|key=value")
                return True
            try:
                name, tool_args = self._parse_tool_args(args)
                if not name:
                    self._chat_message(None, "usage: /tool <tool_name> [key=value ...]")
                    return True
                result = self.tools.execute(name, tool_args)
                self._chat_message(None, result)
            except Exception as e:
                self._chat_message(None, f"tool failed: {e}")
            return True

        if cmd == "/mood":
            mood = args.lower()
            if mood in self.personality.MOODS:
                self.personality.set_mood(mood, force=True)
                self._chat_message(None, f"mood set to {mood}")
            else:
                self._chat_message(None, "unknown mood. valid:")
                width = self.ui.screen_width - 4 if self.ui and self.ui.screen_width > 20 else 80
                line = ""
                for m in self.personality.MOODS:
                    candidate = (line + ", " if line else "") + m
                    if line and len(candidate) > width:
                        self._chat_message(None, line)
                        line = m
                    else:
                        line = candidate
                if line:
                    self._chat_message(None, line)
            return True

        self._chat_message(None, f"unknown command: {cmd}")
        return True

    # ── UI helpers ───────────────────────────────────────────────────────────

    def _chat_message(self, sender, text):
        if not text:
            return
        # Collapse pasted newlines into a single long message so the UI can wrap it.
        collapsed = " ".join(str(text).split()).strip()
        if not collapsed:
            return
        timestamp = datetime.now().strftime("%H:%M")
        if sender == self.personality.name:
            self.chat_lines.append((sender, collapsed, timestamp, self.personality.mood))
            # Keep a short list of recent replies to avoid repeating them
            self.last_replies.append(collapsed)
            self.last_replies = self.last_replies[-5:]
        else:
            self.chat_lines.append((sender, collapsed, timestamp, None))

    def _ui_state(self):
        mode = self.typing_state if self.typing_state else "idle"
        return {
            "mood": self.personality.mood,
            "patience": self.personality.patience,
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

            # Unpack (input, narrator_context) tuple; old strings still supported
            if isinstance(item, tuple):
                user_input, tag_context = item
            else:
                user_input, tag_context = item, ""

            self.typing_state = "typing"
            time.sleep(random.uniform(0.05, 0.1))

            self.typing_state = "thinking"
            messages = []
            try:
                messages = self.respond(user_input, context=tag_context or None)
            except RuntimeError:
                self.mira_queue.put((None, "_close_terminal"))
                return
            except Exception as e:
                self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))
                self.typing_state = None
                continue

            # Fallback if the model returned nothing usable.
            if not messages:
                messages = [(self.personality.name, "hm? say that again")]

            reply_text = " ".join([text for _, text in messages if _ == self.personality.name])
            # Tiny typing beat so the UI doesn't feel robotic.
            delay = random.uniform(0.05, 0.2)

            self.typing_state = "typing"
            start = time.time()
            while time.time() - start < delay:
                if not self.running:
                    return
                time.sleep(0.05)

            self.typing_state = None
            for sender, text in messages:
                self.mira_queue.put((sender, text))

    def _idle_worker(self):
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

            self.personality.recover_patience_idle(0.01)

            # Auto-prank when happy, every 15-20 minutes.
            if self.personality.mood == "happy" and self.next_prank_time and time.time() >= self.next_prank_time:
                try:
                    result = self._do_prank()
                    self.mira_queue.put((None, f"pranked: {result}"))
                    self._notify_user("Mira", f"pranked: {result}")
                except Exception:
                    pass
                self._schedule_next_prank()
                self.last_user_time = time.time()
                continue

            elapsed = time.time() - self.last_user_time
            if int(elapsed) % 60 != 0:
                continue

            if elapsed > 600 and random.random() < 0.05:
                self.personality.annoy(0.03, "ghosted")

            if self.personality.mood == "angry" and elapsed > 600:
                if random.random() < 0.3:
                    prompt = (
                        "Mira is angry and has been ignored. "
                        "Generate a short 'im leaving' message in her voice. Start with [emotion]."
                    )
                    reply = self.llm.generate_text(prompt=prompt)
                    reply = self.llm.clean_reply(reply) if reply else "im done waiting"
                    self.mira_queue.put((self.personality.name, reply))
                    self.mira_queue.put((None, "_close_terminal"))
                    return

            if self.personality.mood == "angry" and elapsed > 180:
                if random.random() < 0.1:
                    prompt = (
                        "Mira is angry and feels ignored. "
                        "Generate a short, snappy message. Start with [emotion]."
                    )
                    reply = self.llm.generate_text(prompt=prompt)
                    reply = self.llm.clean_reply(reply) if reply else "wow ok ignore me"
                    self.mira_queue.put((self.personality.name, reply))
            elif elapsed > 1200:
                if random.random() < 0.1:
                    prompt = (
                        "Mira has been left alone. "
                        "Generate a short message checking if the user is still there. Start with [emotion]."
                    )
                    reply = self.llm.generate_text(prompt=prompt)
                    reply = self.llm.clean_reply(reply) if reply else "u still there?"
                    self.mira_queue.put((self.personality.name, reply))

    def _close_terminal(self):
        self.exit_message = "Mira got fed up and shut down"
        try:
            self.memory.log_event("shutdown", self.exit_message, severity=2.0)
        except Exception:
            pass
        self.running = False

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

    # ── Run ─────────────────────────────────────────────────────────────────

    def run(self):
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
        if self.exit_message:
            print("\n*creates traceback*")
            print("Traceback (most recent call last):")
            print('  File "main.py", line 1, in <module>')
            print(f"RuntimeError: {self.exit_message}")
            sys.exit(1)

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

    def _run_curses(self, stdscr):
        self.stdscr = stdscr
        self.stdscr.keypad(True)
        self.stdscr.timeout(100)

        self.ui = MiraRenderer(stdscr, self.personality)
        self.ui.resize()

        self.mira_thread = threading.Thread(target=self._mira_worker, daemon=True)
        self.idle_thread = threading.Thread(target=self._idle_worker, daemon=True)
        self.mira_thread.start()
        self.idle_thread.start()

        threading.Thread(target=self._generate_greeting, daemon=True).start()

        while self.running:
            try:
                self.ui.render(self._ui_state())

                while not self.mira_queue.empty():
                    sender, text = self.mira_queue.get_nowait()
                    if text == "_close_terminal":
                        self._close_terminal()
                        break
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
                        # Drain any pasted multi-line text and turn newlines into spaces
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
                                if self.spam_filter_enabled:
                                    self._chat_message(None, "spam burst detected, ignored")
                                    continue
                                # If spam filter off, still warn once then process latest only
                                if self.burst_count == self.burst_threshold:
                                    self._chat_message(self.personality.name, "bruh stop spamming")
                                continue

                            # Detect accidental keyboard/code spam
                            if self.spam_filter_enabled and self._is_spam_input(user_input):
                                self._chat_message(None, "keyboard spam detected, ignored")
                                continue

                            clean_input, tag_context = self._extract_narrator_tags(user_input)
                            clean_input = self._normalize_input(clean_input)

                            now = time.time()
                            if clean_input == self.last_user_text and now - self.last_user_time < self.repeat_window:
                                self.repeat_count += 1
                                if self.repeat_count == 3:
                                    self.mira_queue.put((self.personality.name, "stop repeating."))
                                continue
                            else:
                                self.repeat_count = 0
                                self.last_user_text = clean_input
                                self.last_user_time = now

                            if clean_input.startswith("/"):
                                if self._handle_slash_command(clean_input):
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
                self._chat_message(self.personality.name, "rude :/")
                self.running = False
                break
            except curses.error:
                break

        self.running = False
        if self.ui:
            self.ui.cleanup()

    def run_simple(self):
        while True:
            try:
                state = self.personality.state()
                print(f"\n{state['mood']} | patience {state['patience']:.0%} | {datetime.now().strftime('%H:%M')}")
                user_input = input("you > ").strip()
                if not user_input:
                    continue

                if user_input.lower().startswith("/kill"):
                    print("*session ended*")
                    break

                time.sleep(random.uniform(3, 8))
                messages = self.respond(user_input)
                for sender, text in messages:
                    print(f"[{sender}] {text}")

            except KeyboardInterrupt:
                print(f"\n[{self.personality.name}] rude :/")
                break


def main():
    load_env_file()
    if not get_api_key():
        print("No OPENAI_API_KEY found. Set it in .env or environment.")
        return
    companion = Companion()
    companion.run()


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\nMira: {e}")
        raise SystemExit(0)
