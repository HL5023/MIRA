#!/usr/bin/env python3
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from brain.llm import LLM, load_env_file, get_api_key
from brain.memory import Memory
from brain.personality import Personality
from brain.tools import Tools


def load_config(path: str = "config.json") -> dict:
    return json.loads(Path(path).read_text())


# Common English words that fuzzy-match insults (e.g. 'work' ~ 'dork', 'will' ~ 'kill').
# Exact matches still count; only fuzzy matching is skipped for these.
COMMON_WORDS = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for",
    "not", "on", "with", "he", "as", "you", "do", "at", "this", "but", "his",
    "by", "from", "they", "we", "say", "her", "she", "or", "an", "will", "my",
    "one", "all", "would", "there", "their", "what", "so", "up", "out", "if",
    "about", "who", "get", "which", "go", "me", "when", "make", "can", "like",
    "time", "no", "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then", "now", "look",
    "only", "come", "its", "over", "think", "also", "back", "after", "use", "two",
    "how", "our", "work", "first", "well", "way", "even", "new", "want", "because",
    "any", "these", "give", "day", "most", "us", "been", "has", "had", "were",
    "are", "was", "did", "does", "doing", "said", "says", "going", "goes",
    "got", "getting", "made", "making", "knew", "thought", "saw", "seen", "came",
    "took", "taken", "wanted", "needed", "liked", "loved", "hated", "played",
    "playing", "reading", "watching", "listening", "lived", "living", "studying",
    "working", "job", "home", "house", "cat", "dog", "friend", "friends",
    "family", "mom", "dad", "brother", "sister", "name", "called", "named", "call",
    "tell", "told", "talk", "talked", "talking", "ask", "asked", "asking",
    "help", "helped", "helping", "try", "tried", "trying", "start", "started",
    "starting", "stopped", "stopping", "keep", "kept", "keeping", "feel",
    "felt", "feeling", "seem", "seemed", "seeming", "leave", "left", "leaving",
    "put", "set", "let", "run", "ran", "running", "move", "moved", "moving",
    "turn", "turned", "turning", "opened", "opening", "closed", "closing", "find",
    "found", "finding", "gave", "given", "bring", "brought", "bringing", "hold",
    "held", "holding", "wrote", "writing", "sat", "sitting", "stood", "standing",
    "lost", "losing", "paid", "paying", "met", "meeting", "included", "continued",
    "learned", "learning", "changed", "changing", "led", "leading", "understood",
    "followed", "following", "created", "creating", "spoke", "speaking", "allowed",
    "added", "spent", "spending", "grew", "growing", "walked", "walking", "won",
    "winning", "offered", "remembered", "remembering", "considered", "appeared",
    "bought", "buying", "served", "died", "dying", "sent", "sending", "expected",
    "built", "building", "stayed", "staying", "fell", "falling", "cutting", "reached",
    "reaching", "killed", "killing", "raised", "raising", "passed", "passing", "sold",
    "selling", "decided", "deciding", "returned", "returning", "explained", "explaining",
    "hoped", "hoping", "developed", "carried", "carrying", "broke", "breaking",
    "received", "agreed", "supported", "hitting", "produced", "ate", "eating", "covered",
    "caught", "catching", "drew", "drawing", "chose", "choosing", "picked", "picking",
    "kicked", "kicking", "sick", "tick", "lick", "thick", "quick", "duck", "luck",
    "suck", "truck", "stuck", "ship", "shot", "tie", "lie", "pie", "where", "rate",
    "tape", "cape", "hunt", "slot", "salt", "crash", "flash", "laser", "fork", "cork",
    "pork", "dark", "dock", "deck", "dump", "numb", "dame", "damp", "dawn", "batch",
    "botch", "butch", "beach", "bench", "whole", "shore", "bill", "fill", "hill",
    "till", "mill", "pill", "sill", "mira", "mra", "mirra", "hi", "hey", "hello",
    "yo", "sup", "hii", "heyy", "ok", "okay", "yeah", "yes", "no", "nah", "bruh",
    "bro", "dude", "man", "girl", "boy", "lol", "lmao", "omg", "idk", "tbh", "btw",
}


def extract_facts(text: str) -> list:
    """Pull short personal facts from user messages. Skip code dumps and long rambles."""
    if len(text) > 300 or text.count("\n") > 3 or "def " in text or "class " in text:
        return []

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
    facts = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            fact = match.group(0).strip()
            if len(fact) > 120 or any(c in fact for c in "{}"):
                continue
            facts.append(fact)
    return facts


class MiraCore:
    def __init__(self):
        self.config = load_config()
        self.personality = Personality()
        self.memory = Memory()
        self.tools = Tools()
        self.llm = LLM()

        self.recent_inputs = []
        self.last_user_messages = []
        self.spam_filter_enabled = True
        self.exit_message = None

        # Anti-repeat tracking
        self.last_normalized_input = ""
        self.normalized_repeat_count = 0
        self.max_repeats_before_silent = 4

        # Recently touched files for "edit that" / "edit <name>" references
        self.recent_files = []
        self._max_recent_files = 10

        # Track last explicit topic for /teach and "i don't understand" handoffs
        self.last_topic = None
        self.last_question = None
        self.last_handoff_topic = None

        # Recent Mira replies to prevent repetition
        self.last_replies = []

        # Write history for /undo
        self.write_history = []

        # Config-driven settings (fall back to sensible defaults)
        self.settings = self.config.get("settings", {})
        self.max_repeats_before_silent = int(self.settings.get("max_repeats_before_silent", 4))

        # Action-only replies (e.g. <cries>) are logged but not shown in chat
        self.recent_actions = []

        # Session lifecycle
        self.running = True
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Prank / auto-mischief scheduling
        self.next_prank_time = None
        self._schedule_next_prank()

        # Slash command dispatch table
        self._slash_handlers = {
            "/help": self._cmd_help,
            "/time": self._cmd_time,
            "/spam": self._cmd_spam,
            "/memory": self._cmd_memory,
            "/status": self._cmd_status,
            # "/persona": self._cmd_persona,  # personality editor disabled for now
            "/exec": self._cmd_exec,
            "/teach": self._cmd_teach,
            "/prank": self._cmd_prank,
            "/read": self._cmd_read,
            "/write": self._cmd_write,
            "/edit": self._cmd_edit,
            "/list": self._cmd_list,
            "/tool": self._cmd_tool,
            "/tools": self._cmd_tool,
            "/mood": self._cmd_mood,
            "/forget": self._cmd_forget,
            "/relationship": self._cmd_relationship,
            "/undo": self._cmd_undo,
        }

    # ── Memory helpers ───────────────────────────────────────────────────

    def save_fact_from_user(self, text: str):
        # Fast regex path for obvious facts (no LLM cost)
        for fact in extract_facts(text):
            self.memory.remember_fact("user", fact)
        # LLM path for anything that looks like it may contain a fact
        if self._looks_like_fact(text) and not self._is_spam_input(text):
            threading.Thread(target=self._extract_facts_llm, args=(text,), daemon=True).start()

    def _looks_like_fact(self, text: str) -> bool:
        """Cheap gate: does this message plausibly contain a personal fact?
        Only messages that pass this trigger the (more expensive) LLM extractor."""
        lower = text.lower()
        signals = [
            "my ", "i am", "i'm", "im ", "i like", "i love", "i hate", "i have", "i work",
            "i study", "i want", "i need", "i feel", "i think", "i live", "i play",
            "i do", "i go", "i use", "i read", "i watch", "i listen", "i'm from",
            "my name", "my favorite", "my birthday", "my age", "i was born", "i used to",
        ]
        return any(s in lower for s in signals)

    def _extract_facts_llm(self, text: str):
        """Use the LLM to extract personal facts from a message (background thread)."""
        try:
            prompt = (
                "Extract stable personal facts about the user from this message. "
                "A fact is a durable detail: name, job, school, family, pets, likes, dislikes, plans, etc. "
                "Ignore greetings, questions, jokes, and transient statements. "
                "Return ONLY a JSON array of short strings, e.g. [\"Derek's cat is named Mochi\"]. "
                "If there are no facts, return [].\n\nMessage: " + text
            )
            raw = self._llm_text(prompt)
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not match:
                return
            facts = json.loads(match.group(0))
            for fact in facts:
                if isinstance(fact, str) and 3 <= len(fact) <= 120:
                    self.memory.remember_fact("user", fact)
        except Exception:
            pass

    def _schedule_next_prank(self):
        """Schedule the next automatic prank when Mira is happy."""
        lo = float(self.settings.get("prank_interval_min", 900))
        hi = float(self.settings.get("prank_interval_max", 1200))
        self.next_prank_time = time.time() + random.uniform(lo, hi)  # 15-20 minutes by default

    def _frontmost_app(self) -> str:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True, text=True, check=True,
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
            url = f"https://chatgpt.com/?temporary-chat=true&q={urllib.parse.quote(text)}"
            self.tools.execute("open_website", {"url": url})
        except Exception:
            pass

    # ── Pranks ───────────────────────────────────────────────────────────

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
        messages = ["mira was here", "why r u reading this", "get back to work lol", "mira >>>"]
        self.tools.execute("set_clipboard", {"text": random.choice(messages)})
        return "changed ur clipboard"

    # ── Prompt building ──────────────────────────────────────────────────

    def _user_mood_context(self, user_input: str) -> str:
        """Detect the user's emotional state and tell Mira how to respond."""
        lower = user_input.lower()
        if any(w in lower for w in ["stressed", "overwhelmed", "anxious", "worried", "panic",
                                    "burned out", "burnedout", "so much on my plate", "freaking out"]):
            return "The user seems stressed and overwhelmed right now. Be gentle, reassuring, and don't pile on."
        if any(w in lower for w in ["sad", "depressed", "cry", "upset", "lonely", "heartbroken",
                                    "miserable", "having a bad day", "down", "rough day"]):
            return "The user sounds sad or down right now. Be soft, caring, and offer comfort."
        if any(w in lower for w in ["happy", "great", "awesome", "amazing", "excited", "yay",
                                    "so good", "best day", "love it"]):
            return "The user is in a good mood right now. Match their energy, be cheerful."
        if any(w in lower for w in ["angry", "mad", "pissed", "furious", "so annoyed", "hate this", "i hate"]):
            return "The user is angry/frustrated about something. Don't take it personally; let them vent."
        if any(w in lower for w in ["tired", "exhausted", "sleepy", "drained", "worn out", "no energy"]):
            return "The user is tired and low-energy. Be low-key and not too demanding."
        return ""

    def _time_context(self) -> str:
        """Time-of-day context so Mira acts accordingly."""
        hour = datetime.now().hour
        if hour < 5:
            return "It's the middle of the night. You're sleepy and a bit loopy. Short, quiet replies."
        if hour < 9:
            return "It's early morning. You just woke up and are groggy. Short, arguably grumpy replies."
        if hour < 12:
            return "It's morning. You're awake but not fully caffeinated yet."
        if hour < 17:
            return "It's afternoon. You're in a fairly normal mood."
        if hour < 22:
            return "It's evening. You're relaxed and a bit more chatty."
        return "It's late night. You're getting sleepy but still up for talking."

    def _pattern_context(self) -> str:
        """Notice patterns in when the user talks to Mira."""
        from collections import Counter
        user_msgs = [i for i in self.memory.recent_interactions(60) if i.get("role") == "user"]
        if len(user_msgs) < 5:
            return ""
        hours = []
        for i in user_msgs:
            try:
                hours.append(datetime.fromisoformat(i["timestamp"]).hour)
            except (KeyError, ValueError):
                continue
        if not hours:
            return ""
        common_hour, count = Counter(hours).most_common(1)[0]
        if count < 3:
            return ""
        if common_hour >= 22 or common_hour < 4:
            return "You've noticed the user usually talks to you late at night. It's kind of your little routine now."
        if common_hour < 9:
            return "You've noticed the user usually talks to you early in the morning."
        return "You've noticed the user tends to message you around " + f"{common_hour}:00" + "."

    def _grudge_context(self) -> str:
        """Build a natural emotional memory summary for the LLM."""
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

        return "[MEMORY] " + " ".join(parts) if parts else ""

    def _build_context(self, user_input: str, context: str = None) -> tuple:
        """Assemble shared context (recent msgs, memory, grudge) for both prompt styles."""
        state = self.personality.state()
        recent = self.memory.recent_interactions(10, session_id=self.session_id)
        recent_user_msgs = [r["message"] for r in recent if r.get("role") == "user"][-5:]

        parts = []
        if recent_user_msgs:
            parts.append("Recent messages from the user: " + "; ".join(recent_user_msgs))
        if context:
            parts.append(context)

        rel = self.personality.relationship_context()
        if rel:
            parts.append(rel)

        user_mood = self._user_mood_context(user_input)
        if user_mood:
            parts.append(user_mood)

        time_ctx = self._time_context()
        if time_ctx:
            parts.append(time_ctx)

        pattern_ctx = self._pattern_context()
        if pattern_ctx:
            parts.append(pattern_ctx)

        grudge = self._grudge_context()
        memory_summary = self.memory.memory_summary(10)
        if grudge or memory_summary:
            mem = grudge
            if memory_summary:
                mem = f"[MEMORY]\n{memory_summary}\n{mem}" if mem else f"[MEMORY]\n{memory_summary}"
            parts.append(mem)

        return state, recent, "\n".join(parts) if parts else None

    def _prompt_kwargs(self, state, recent, context, user_input) -> dict:
        return dict(
            name=state["name"],
            voice=self.personality.voice,
            traits=self.personality.traits,
            mood=state["mood"],
            patience=state["patience"],
            recent=recent,
            facts=self.memory.facts_about("user") + self.memory.facts_about("mira"),
            user_input=user_input,
            context=context,
            character_profile=self.personality.character_profile,
            user_profile=self.personality.user_profile,
        )

    def build_prompt(self, user_input: str, context: str = None) -> str:
        state, recent, ctx = self._build_context(user_input, context)
        return self.llm.build_prompt(**self._prompt_kwargs(state, recent, ctx, user_input))

    def build_messages(self, user_input: str, context: str = None) -> list:
        state, recent, ctx = self._build_context(user_input, context)
        return self.llm.build_messages(**self._prompt_kwargs(state, recent, ctx, user_input))

    def _tool_rules(self) -> str:
        tools = ", ".join(t["function"]["name"] for t in self.tools.definitions())
        return (
            "\n\nTOOL RULES:\n"
            "- Use write_file/read_file/edit_file/delete_file/list_files for file operations.\n"
            "- If asked to EDIT an existing file, call read_file first, then use edit_file. Do NOT create a new file.\n"
            "- If the user asks you to read a file, always use read_file. Do not guess or hallucinate the contents.\n"
            "- If asked to CREATE a file, use write_file. Pick a short, safe filename.\n"
            "- Never dump long documents in the chat; save them to ~/Desktop/MiraFiles/.\n"
            "- If the user names or asks about an academic topic, do NOT explain it yourself. Hand them off to ChatGPT with ask_chatgpt or open_chatgpt immediately.\n"
            "- execute_command only when the user explicitly asks for a terminal command.\n"
            "- Reply in one short message. Never split a single reply into multiple chat lines.\n"
            f"Available tools: {tools}."
        )

    def _llm_text(self, prompt: str) -> str:
        """Generate text, handling both openai (messages) and local (prompt) providers."""
        if self.llm.provider == "openai":
            return self.llm.generate_text(messages=[{"role": "user", "content": prompt}])
        return self.llm.generate_text(prompt=prompt)

    def _say(self, prompt: str, fallback: str = "") -> str:
        """Generate a short, in-character reply via the LLM (full context).
        Returns the fallback text ONLY if the AI fails, so Mira never hangs."""
        try:
            if self.llm.provider == "openai":
                reply = self.llm.generate_text(messages=self.build_messages(prompt))
            else:
                reply = self.llm.generate_text(prompt=self.build_prompt(prompt))
            reply = self.llm.clean_reply(reply).strip()
            return reply or fallback
        except Exception:
            return fallback

    # ── Intent detection (deterministic, no per-message LLM call) ────────

    def _detect_insult(self, user_input: str) -> float:
        """Detect hostile intent and return an intensity between 0.0 and 0.5."""
        lower = user_input.lower()
        directed_at_mira = any(p in lower for p in ("you ", "u ", "mira", "your ", "ur "))
        words = re.findall(r"\b\w+\b", lower)

        mild = {"stupid", "idiot", "dumb", "freak", "jerk", "weirdo", "moron", "loser",
                "dork", "dweeb", "trash", "garbage", "annoying", "shut", "gtfo"}
        moderate = {"bitch", "ass", "asshole", "dick", "dickhead", "damn", "shit", "bastard",
                    "worthless", "pathetic", "dumbass", "jackass", "dipshit", "bullshit",
                    "shithead", "douchebag"}
        severe = {"fuck", "fucker", "fucking", "cunt", "whore", "slut", "kill", "die",
                  "kys", "retard", "rape", "motherfucker", "motherfucking"}

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

        safe_words = COMMON_WORDS

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

    def _detect_apology(self, user_input: str) -> bool:
        """Detect a genuine apology (replaces the old per-message LLM call)."""
        lower = user_input.lower()
        patterns = [
            "im sorry", "i'm sorry", "i am sorry", "i apologize", "i apologise",
            "my bad", "my fault", "forgive me", "sorry for", "sorry about",
            "i didnt mean", "i didn't mean", "i was wrong", "i was being",
        ]
        return any(p in lower for p in patterns)

    def _is_edit_request(self, user_input: str) -> bool:
        lower = user_input.lower()
        return any(p in lower for p in ["edit that", "edit this", "edit the document", "edit the file", "edit it", "update that", "change that"])

    def _wants_chatgpt_handoff(self, user_input: str) -> bool:
        lower = user_input.lower()
        return any(p in lower for p in [
            "ask chatgpt", "ask gpt", "ask chat gpt", "let chatgpt", "let gpt",
            "get chatgpt to", "have chatgpt", "can chatgpt", "could chatgpt",
        ])

    def _is_teaching_request(self, user_input: str) -> bool:
        """Detect if the user is asking Mira to explain or teach a topic."""
        lower = user_input.lower().strip()
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
        return any(re.search(p, lower) for p in patterns)

    def _is_spam_input(self, user_input: str) -> bool:
        """Detect accidental paste spam, code dumps, and keyboard mashing."""
        if len(user_input) > 1000 or user_input.count("\n") > 5:
            return True
        if "def " in user_input and len(user_input) > 200:
            return True

        if len(user_input) > 50:
            words = user_input.split()
            if words:
                avg_word_len = sum(len(w) for w in words) / len(words)
                if avg_word_len > 15:
                    return True

            if len(user_input) > 100:
                common = {"the", "and", "a", "an", "of", "to", "in", "is", "it", "you", "i", "me", "my", "for", "that", "this", "with", "as", "on", "at"}
                lower_words = set(re.findall(r"\b\w+\b", user_input.lower()))
                if not lower_words & common:
                    return True

            letters = sum(1 for c in user_input if c.isalpha())
            non_letters = sum(1 for c in user_input if not c.isalpha() and not c.isspace())
            if letters > 0 and non_letters / letters > 0.8:
                return True

        return False

    def _extract_topic_for_teach(self, user_input: str) -> str:
        """Extract a clean topic from a 'don't understand' or /teach message."""
        text = user_input
        phrases = [
            r"don't understand", r"dont understand", r"not understand",
            r"confused", r"explain again", r"teach me about", r"teach me",
            r"explain to me", r"explain", r"tell me about", r"help me understand",
            r"what is", r"what are", r"how does", r"how do", r"how to",
            r"can you", r"can u", r"could you", r"could u", r"would you", r"will you",
            r"please", r"about", r"this", r"that",
        ]
        for p in phrases:
            text = re.sub(re.escape(p), " ", text, flags=re.IGNORECASE)

        text = re.sub(r"\b(to me|for me|from you|by you|please|the|a|an|is|are|do|does|did|can|could|would|will|u|you)\b", " ", text, flags=re.IGNORECASE)
        text = text.strip(" ,.!?;:\-\"")
        text = " ".join(text.split())

        if not text and self.last_question:
            text = self.last_question
        return text or "this topic"

    def _extract_narrator_tags(self, text: str):
        """Return (visible_text, context) where {...} is hidden context."""
        tags = re.findall(r"\{([^}]*)\}", text)
        visible = re.sub(r"\{[^}]*\}", "", text)
        visible = " ".join(visible.split())
        context = "\n".join(tag.strip() for tag in tags if tag.strip())
        return visible, context

    def _handoff_to_chatgpt(self, topic: str, user_input: str) -> list:
        """Open ChatGPT temp chat and return a reluctant, AI-generated reply."""
        topic = topic or "this topic"

        if self.last_handoff_topic and self.last_handoff_topic.lower() == topic.lower():
            prompt = (
                "Mira is a terminal-based companion. The user asked about the same topic again, "
                "but she already opened ChatGPT for it. Generate a very short, slightly angry reply. "
                "Use lowercase, slang, and text emojis. Start with [emotion]."
            )
            reply = self._llm_text(prompt)
            reply = self.llm.clean_reply(reply) if reply else "already asked chatgpt bruh"
            return [(self.personality.name, reply)]

        self.last_handoff_topic = topic
        threading.Timer(0.5, self._open_chatgpt_temp, args=(topic,)).start()

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
        reply = self._llm_text(prompt)
        reply = self.llm.clean_reply(reply) if reply else random.choice(fallbacks)
        return [(self.personality.name, reply)]

    def _handle_memory_question(self, user_input: str) -> list:
        """Answer factual memory questions with exact data instead of letting the LLM guess."""
        lower = user_input.lower()
        count_patterns = [
            r"how many times.*insult",
            r"how many times.*(been mean|swore|curse|cuss)",
            r"how many insults",
            r"count.*insults",
            r"how many times.*pissed.*(you|u) off",
        ]
        if any(re.search(p, lower) for p in count_patterns):
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

        # Semantic recall: "what did I say about X" / "do you remember X"
        topic = self._extract_recall_topic(user_input)
        if topic:
            matches = self._search_memory(topic, exclude=user_input)
            if matches:
                reply = f"yeah, u said: {matches[0]}"
                if len(matches) > 1:
                    reply += f" (and {len(matches) - 1} more)"
                return [(self.personality.name, reply)]
            return [(self.personality.name, f"hmm, i dont remember u saying anything about {topic}")]

        return None

    RECALL_PHRASES = [
        "do you remember", "do u remember", "what did i say", "what did i tell",
        "when did i tell", "when did i say", "what do you know", "what do you remember",
        "what did i mention", "when did i mention", "did i tell you", "did i mention",
    ]

    def _extract_recall_topic(self, user_input: str) -> str:
        """Extract the topic from a recall question, or '' if it isn't one."""
        lower = user_input.lower()
        if not any(p in lower for p in self.RECALL_PHRASES):
            return ""
        text = lower
        for p in self.RECALL_PHRASES:
            text = text.replace(p, " ")
        text = re.sub(r"\b(about|that|re|regarding|with|me|u|you|i)\b", " ", text)
        text = re.sub(r"[?.,!]", "", text)
        return " ".join(text.split())

    def _search_memory(self, topic: str, exclude: str = "") -> list:
        """Search facts and recent user messages for the topic, excluding the current message.
        Uses exact term matching first (with synonyms), then character n-gram similarity
        as a semantic fallback for near-matches."""
        topic_lower = topic.lower()
        exclude_lower = exclude.lower()

        stopwords = {"my", "your", "the", "a", "an", "about", "that", "this", "me", "u",
                     "you", "i", "it", "of", "for", "with", "to", "in", "on", "and"}
        words = [w for w in re.findall(r"\w+", topic_lower) if w not in stopwords and len(w) > 2]
        synonyms = {
            "job": ["work", "works", "working", "career", "engineer"],
            "work": ["job", "career"],
            "cat": ["cats", "kitten"],
            "dog": ["dogs", "puppy"],
            "school": ["college", "university", "class"],
            "music": ["song", "songs", "band"],
            "game": ["games", "gaming"],
            "movie": ["movies", "film", "show"],
            "food": ["eat", "eating", "cooking"],
        }
        terms = [topic_lower] + words
        for w in words:
            terms += synonyms.get(w, [])
        terms = list(dict.fromkeys(t for t in terms if t))
        if not terms:
            return []

        candidates = list(self.memory.all_facts())
        for inter in self.memory.recent_interactions(200):
            msg = inter.get("message", "")
            if inter.get("role") == "user" and msg.lower() != exclude_lower:
                candidates.append(msg)

        # Exact term match first
        exact = [c for c in candidates if any(t in c.lower() for t in terms)]
        if exact:
            return self._dedupe(exact)[:3]

        # Semantic fallback: character n-gram similarity
        scored = []
        for cand in candidates:
            sim = max((self._ngram_similarity(t, cand) for t in terms), default=0.0)
            if sim >= 0.35:
                scored.append((sim, cand))
        scored.sort(key=lambda x: x[0], reverse=True)
        return self._dedupe([c for _, c in scored])[:3]

    @staticmethod
    def _dedupe(items: list) -> list:
        seen, out = set(), []
        for i in items:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    @staticmethod
    def _ngram_similarity(a: str, b: str, n: int = 3) -> float:
        """Character n-gram Jaccard similarity between two strings (0.0-1.0)."""
        if not a or not b:
            return 0.0

        def ngrams(s: str):
            s = s.lower()
            if len(s) < n:
                return {s}
            return {s[i:i + n] for i in range(len(s) - n + 1)}

        ga, gb = ngrams(a), ngrams(b)
        union = ga | gb
        if not union:
            return 0.0
        return len(ga & gb) / len(union)

    # ── Main response loop ───────────────────────────────────────────────

    def respond(self, user_input: str, context: str = None) -> list:
        if user_input.startswith("/"):
            return self._handle_slash_command(user_input)

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
            return [(self.personality.name, self._say(
                "The user just sent the exact same message twice in a row. "
                "Reply very briefly and slightly annoyed that they're repeating themselves. One short line.",
                "yeah u said that"))]
        if self.normalized_repeat_count == 3:
            return [(self.personality.name, self._say(
                "The user has sent the same message three times in a row. "
                "Reply annoyed and snappy, tell them to stop. One short line.",
                "bruh stop"))]
        if self.normalized_repeat_count >= self.max_repeats_before_silent:
            self.memory.log_event("action", "sighs")
            return []

        annoy_amount = self._detect_insult(user_input)
        is_apology = self._detect_apology(user_input)

        memory_reply = self._handle_memory_question(user_input)
        if memory_reply is not None:
            return memory_reply

        if is_apology:
            # Apologies recover extra patience and ease anger/sadness
            self.personality.comfort(0.10)
            self.personality.adjust_relationship(apology=True)
            if self.personality.mood in ("angry", "sad"):
                self.personality.set_mood("calm", force=True)
            annoy_amount = 0.0

        # Apply patience effects: insults drain, friendly chat recovers.
        if annoy_amount > 0:
            cost = 0.03 + min(annoy_amount, 0.25) * (1.0 + (1.0 - self.personality.patience) * 0.8)
            if self.personality.patience - cost <= 0:
                self.personality.set_mood("angry", force=True, trigger="shutdown")
                self.exit_message = self._generate_close_message("angry")
                raise RuntimeError("Mira ran out of patience and shut down")
            self.personality.annoy(annoy_amount, "insult")
            self.personality.adjust_relationship(insult=annoy_amount)
            self.memory.log_event("insult", user_input, severity=annoy_amount)
        else:
            self.personality.comfort(0.02)
            self.personality.adjust_relationship(kind=True)

        # Refuse helpful tool-based or teaching requests while angry or sad.
        if self.personality.mood in ("angry", "sad"):
            if self._is_teaching_request(user_input) or self._is_edit_request(user_input) or self._wants_chatgpt_handoff(user_input):
                return [(self.personality.name, self._say(
                    "You're angry/sad at the user. They just asked you to help with something, but you refuse "
                    "because of how they've treated you. Reply in-character, refusing and demanding comfort or "
                    "an apology first. One short line.",
                    "u really think im gonna help u after that? nah. make me feel better first."))]

        is_hostile = annoy_amount > 0
        lower_input = user_input.lower()

        # Confusion / teaching / ChatGPT handoffs
        confusion_phrases = ["dont understand", "don't understand", "not understand", "confused", "explain again"]
        if not is_hostile and any(p in lower_input for p in confusion_phrases):
            return self._handoff_to_chatgpt(self._extract_topic_for_teach(user_input), user_input)
        if not is_hostile and self._wants_chatgpt_handoff(user_input):
            topic = self.last_question or self.last_topic or "this topic"
            return self._handoff_to_chatgpt(topic, user_input)
        if not is_hostile and self._is_teaching_request(user_input):
            return self._handoff_to_chatgpt(self._extract_topic_for_teach(user_input), user_input)
        if self._is_edit_request(user_input):
            return self._handle_edit_request(user_input)

        reply = ""
        reply_actions = []
        try:
            messages = self.build_messages(user_input, context=context)
            recent_replies = "\n".join(f"- {r}" for r in self.last_replies[-3:])
            anti_repeat = f"\n\nDo NOT repeat these recent replies:\n{recent_replies}\n" if recent_replies else ""
            messages[0]["content"] += anti_repeat + self._tool_rules()

            final_reply = ""
            last_tool_result = ""
            for _ in range(5):
                result = self.llm.generate(messages=messages, tools=self.tools.definitions())
                content = result.get("content", "")
                tool_calls = result.get("tool_calls") or []

                if not tool_calls:
                    final_reply = content
                    break

                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for tc in tool_calls
                    ],
                })

                # Execute each tool call and feed results back
                write_results = []
                for tc in tool_calls:
                    if self.personality.mood in ("angry", "sad"):
                        tool_result = "refused: im not helping u rn"
                    else:
                        try:
                            args = json.loads(tc["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        self._record_write_history(tc["name"], args)
                        tool_result = self.tools.execute(tc["name"], args)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(tool_result)})
                    last_tool_result = str(tool_result)
                    if tc["name"] in ("write_file", "edit_file"):
                        write_results.append(f"{tc['name']}: {tool_result}")

                # If a file was written/edited, confirm once and stop the tool loop.
                if write_results:
                    final_reply = self._generate_save_confirmation("; ".join(write_results))
                    break
            else:
                if not final_reply:
                    final_reply = self._say(
                        "The task wrapped up after several tool calls. React briefly in-character, one short line.",
                        "done.")

            if not final_reply or not str(final_reply).strip():
                if last_tool_result:
                    final_reply = self._generate_tool_reaction(last_tool_result)
                if not final_reply or not str(final_reply).strip():
                    final_reply = self._say(
                        "A tool operation just finished. React briefly in-character, one short line.",
                        "done.")

            # A strong emotion tag can nudge her mood, but never force-flips it
            # (that caused angry->calm swinging). Only shift when she isn't already
            # in that mood AND the tag differs meaningfully; cooldown still applies.
            tagged_mood = self.llm.extract_emotion_tag(final_reply)
            if tagged_mood and tagged_mood in self.personality.MOODS and tagged_mood != self.personality.mood:
                self.personality.set_mood(tagged_mood)  # not forced — respects cooldown/inertia

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

        if not reply or not reply.strip():
            reply = "" if reply_actions else self._say(
                "The user's message was unclear or you have nothing to say. "
                "Reply briefly, asking what they meant or that you didn't catch that. One short line.",
                "hm? say that again")

        if reply and reply.strip():
            output = [(self.personality.name, reply)]
        elif reply_actions:
            output = [(None, f"mira: <{reply_actions[0]}>")]
        else:
            output = []

        full_reply = "\n".join(line for line in (reply or "").split("\n") if "*" not in line)
        if full_reply:
            self.memory.log_interaction("companion", full_reply, session_id=self.session_id)

        self.personality.update(user_input)
        self.last_question = user_input

        # Notify the user if the terminal is not in front when Mira replies.
        try:
            if reply and self._frontmost_app() not in ("terminal", "iterm", "ghostty", ""):
                self._notify_user("Mira", reply[:80] + ("..." if len(reply) > 80 else ""))
        except Exception:
            pass

        return output

    def respond_stream(self, user_input: str, context: str = None):
        """Generator version of respond() that yields (sender, text, is_delta) tuples.
        The API returns the full reply at once, so each reply is yielded whole
        (is_delta=False). Kept as a generator so the UI can stream later."""
        for sender, text in self.respond(user_input, context=context):
            yield sender, text, False

    def _generate_tool_reaction(self, tool_result: str) -> str:
        try:
            ui = f"tool result: {tool_result}. react to it."
            if self.llm.provider == "openai":
                reply = self.llm.generate_text(messages=self.build_messages(ui))
            else:
                reply = self.llm.generate_text(prompt=self.build_prompt(ui))
            return self.llm.clean_reply(reply)
        except Exception:
            return ""

    def _generate_close_message(self, mood: str) -> str:
        prompts = {"angry": "im done. say you are fed up and leaving with censored swearing. then say bye", "sad": "im tired", "tired": "im too tired"}
        try:
            ui = prompts.get(mood, "bye")
            if self.llm.provider == "openai":
                reply = self.llm.generate_text(messages=self.build_messages(ui))
            else:
                reply = self.llm.generate_text(prompt=self.build_prompt(ui))
            return self.llm.clean_reply(reply) or "im done"
        except Exception:
            return "im done"

    def _close_terminal(self):
        if self.exit_message is None:
            self.exit_message = "Mira got fed up and shut down"
        try:
            self.memory.log_event("shutdown", self.exit_message, severity=2.0)
            self._summarize_session()
        except Exception:
            pass
        self.running = False

    def _summarize_session(self):
        """Summarize the session and store it as a fact so she remembers it next boot."""
        interactions = self.memory.recent_interactions(50, session_id=self.session_id)
        if len(interactions) < 3:
            return
        try:
            lines = [f"- {i['role']}: {i['message'][:80]}" for i in interactions]
            prompt = (
                "Summarize this conversation in 2-3 short sentences from Mira's perspective. "
                "Focus on what happened and how she felt. Keep it casual.\n\n" + "\n".join(lines)
            )
            summary = self._llm_text(prompt)
            summary = self.llm.clean_reply(summary).strip()
            if summary:
                self.memory.remember_fact("mira", f"last session: {summary}")
        except Exception:
            pass

    # ── Document / code generation ───────────────────────────────────────

    DOC_INSTRUCTIONS = {
        "speech": "Write a {wc}-word speech about {topic}. Sound natural when read out loud. Use short sentences, clear ideas, and a conversational tone. Include one relatable example or short story. Avoid formal AI phrases. No meta commentary.",
        "letter": "Write a {wc}-word letter about {topic}. Use a warm, personal voice. Include specific details and feelings. Avoid generic AI phrases. No meta commentary.",
        "story": "Write a {wc}-word short story about {topic}. Use a natural narrative voice, dialogue, and specific details. Avoid clichés. No meta commentary.",
        "article": "Write a {wc}-word article about {topic}. Use a conversational, informative tone with specific examples. Avoid generic AI phrases. No meta commentary.",
        "blog": "Write a {wc}-word blog post about {topic}. Be casual, opinionated, and easy to read. Include a personal take. Avoid generic AI phrases. No meta commentary.",
        "poem": "Write a poem about {topic}. Keep the language simple, vivid, and emotional. Avoid clichés. No meta commentary.",
        "email": "Write a concise email about {topic}. Keep it natural and to the point. Avoid generic AI phrases. No meta commentary.",
        "outline": "Write a clear, numbered outline about {topic}. Use short bullet points or numbered sections. No full prose. No meta commentary.",
        "planner": "Write a practical plan about {topic}. Break it into clear steps, deadlines if relevant, and actionable items. No meta commentary.",
        "planning": "Write a practical plan about {topic}. Break it into clear steps, deadlines if relevant, and actionable items. No meta commentary.",
        "plan": "Write a practical plan about {topic}. Break it into clear steps, deadlines if relevant, and actionable items. No meta commentary.",
    }
    DEFAULT_DOC_INSTRUCTIONS = (
        "Write an essay about {topic}, approximately {wc} words. "
        "Pretend you are a real person texting a friend. Use a natural voice, contractions, "
        "and one small personal anecdote or opinion. Include specific examples. "
        "Avoid generic AI phrases like 'in conclusion', 'in today's world', 'it is important to note that', "
        "'furthermore', 'moreover', 'ultimately', and 'delve'. "
        "Do not use a thesis statement. Do not summarize at the end. "
        "It's okay to be a little informal, imperfect, or even slightly contradictory. No meta commentary."
    )

    def generate_document(self, doc_type: str, topic: str, word_count: int) -> str:
        try:
            template = self.DOC_INSTRUCTIONS.get(doc_type, self.DEFAULT_DOC_INSTRUCTIONS)
            instructions = template.format(topic=topic, wc=word_count)
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
            return self._strip_code_fences(self.llm.generate_text(messages=messages))
        except Exception as e:
            return f"couldn't write code: {e}"

    def _strip_code_fences(self, content: str) -> str:
        """Remove markdown code fences from generated code."""
        lines = content.splitlines()
        while lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines)

    def _clean_filename_base(self, text: str, is_code: bool = False) -> str:
        """Make a clean filename from the user's request."""
        lower = text.lower()

        if is_code:
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
            return "_".join(w.lower() for w in words) if words else "generated"

        lower = re.sub(r"^(write|create|make|generate)\s+(me\s+)?(a|an|the)?\s*", "", lower)
        lower = re.sub(r"^(some|a|an|the)\s+", "", lower)
        lower = re.sub(r"\b\d{3,4}\s*(?:word|words|word-|words-)\b", "", lower)
        lower = re.sub(r"\b\d+\s*min(?:ute)?\b", "", lower)
        words = re.sub(r"[^\w\s-]", "", lower).split()
        while words and words[0] in ("a", "an", "the", "about", "of", "for"):
            words = words[1:]
        words = words[:7]
        return " ".join(w.capitalize() for w in words) if words else "Generated"

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
        filename = f"{base}{ext}" if kind == "code" else f"{base} {kind.title()}{ext}"

        safe_root = Path.home().resolve()

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
        hint = name_hint.strip().lower()
        if hint:
            for p in self.recent_files:
                if hint in p.lower():
                    return p
            for p in self.recent_files:
                if Path(p).name.lower().startswith(hint):
                    return p
        return self.recent_files[0] if self.recent_files else None

    def _find_file_in_save_dir(self, hint: str) -> str:
        """Search ~/Desktop/MiraFiles/ for a file matching the hint."""
        save_dir = Path.home() / "Desktop" / "MiraFiles"
        if not save_dir.exists():
            return None
        hint_lower = hint.lower()
        for path in save_dir.iterdir():
            if path.is_file() and (hint_lower in path.name.lower() or path.name.lower().startswith(hint_lower)):
                return str(path)
        return None

    def _generate_save_confirmation(self, tool_result: str) -> str:
        """Generate a short, in-character confirmation that a file was saved or edited."""
        try:
            prompt = (
                f"A file operation just completed: {tool_result}. "
                "Respond briefly in your own voice, as if you just finished the task. "
                "Mention the file path. No more than one sentence."
            )
            if self.llm.provider == "openai":
                reply = self.llm.generate_text(messages=self.build_messages(prompt))
            else:
                reply = self.llm.generate_text(prompt=self.build_prompt(prompt))
            reply = self.llm.clean_reply(reply)
            if reply:
                return reply
        except Exception:
            pass
        return f"done. saved to {tool_result}."

    def _normalize_input(self, text: str) -> str:
        """Strip typos, trailing asterisks, and repeated letters."""
        text = text.strip().strip("*")
        text = re.sub(r"(.)\1{2,}", r"\1\1", text)
        return " ".join(text.split())

    def _handle_edit_request(self, user_input: str) -> list:
        """Handle 'edit that' / 'edit <filename>' by rewriting the matching recent file."""
        hint = None
        m = re.search(r"edit\s+(?:that|the|this|file)?\s*['\"]?([^'\"]+?\.?[\w]+)['\"]?", user_input, re.IGNORECASE)
        if m:
            hint = m.group(1).strip()

        path_str = self._find_recent_file(hint) if hint else (self.recent_files[0] if self.recent_files else None)
        if not path_str and hint:
            path_str = self._find_file_in_save_dir(hint)
        if not path_str:
            return [(self.personality.name, "no file has been saved yet to edit")]

        path = Path(path_str)
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
            new_content = self._strip_code_fences(new_content)
            result = self.tools.execute("edit_file", {"path": str(path), "content": new_content})
            return [(None, f"tool: {result}")]
        except Exception as e:
            return [(self.personality.name, f"(AI failed: {e})")]

    def _parse_tool_args(self, raw: str) -> tuple:
        raw = raw.strip()
        if not raw:
            return None, {}
        parts = raw.split(None, 1)
        name = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        tool_args = {}
        if not rest:
            return name, tool_args
        pattern = re.compile(r'(\w+?)\s*=\s*("[^"]*"|\'[^\']*\'|\S+)')
        for key, val in pattern.findall(rest):
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            tool_args[key] = val
        return name, tool_args

    # ── Slash commands ───────────────────────────────────────────────────

    def _handle_slash_command(self, user_input: str) -> List[Tuple[Optional[str], str]]:
        parts = user_input.split()
        if not parts:
            return []
        cmd = parts[0].lower()
        args = " ".join(parts[1:])
        handler = self._slash_handlers.get(cmd)
        if handler:
            return handler(args)
        return [(None, f"unknown command: {cmd}")]

    def _cmd_help(self, args: str) -> list:
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
            "  /mood history      show mood history chart",
            "  /persona           show/edit personality (/persona set sarcasm 0.8)",
            "  /prank [type]      do a prank (mouse, window, volume, clipboard, rickroll)",
            "  /teach <topic>     open ChatGPT temporary chat to explain a topic",
            "  /spam              toggle keyboard-spam filter",
            "  /memory            show what Mira remembers",
            "  /forget <fact>     forget a specific fact",
            "  /relationship      show trust/closeness/frustration",
            "  /undo              undo Mira's last file write",
            "  /status            show Mira's current status",
            "  /help              show this help",
            "",
            "tools:",
            "  time, write_file, read_file, edit_file, delete_file, list_files",
            "  execute_command, open_file, open_website, web_search, read_website",
            "  system_info, open_app, close_app, toggle_wifi, toggle_airdrop",
            "  notify, type_text, press_key, get_volume, set_volume",
            "  move_mouse, shake_mouse, click_mouse, get_mouse_position",
            "  get_clipboard, set_clipboard, close_front_window, minimize_front_window, resize_window",
            "  ask_chatgpt, say, screenshot",
            "",
            "examples:",
            "  /tool time",
            "  /tool write_file path=hi.txt content=hello world",
            "  /tool read_file path=hi.txt",
            "  /exec ls -la",
            "  /prank mouse",
            "  /teach quadratic equations",
        ]
        return [(None, line) for line in help_lines]

    def _cmd_time(self, args: str) -> list:
        return [(None, self.tools.execute("time", {}))]

    def _cmd_spam(self, args: str) -> list:
        self.spam_filter_enabled = not self.spam_filter_enabled
        return [(None, f"spam filter {'on' if self.spam_filter_enabled else 'off'}")]

    def _cmd_memory(self, args: str) -> list:
        facts = self.memory.all_facts()[-10:]
        events = self.memory.recent_events(10)
        lines = [f"last {len(facts)} facts:"]
        lines += [f"  - {fact}" for fact in facts]
        lines.append(f"last {len(events)} events:")
        lines += [f"  - {ev.get('event_type')}: {ev.get('detail')}" for ev in events]
        return [(None, line) for line in lines]

    def _cmd_status(self, args: str) -> list:
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
            status_lines += [f"  - {ev.get('event_type')}: {ev.get('detail')}" for ev in recent_events]
        return [(None, line) for line in status_lines]

    # ── Personality editor (DISABLED for now) ──────────────────────────────
    # The personality editor is temporarily disabled pending redesign.
    # The underlying Personality methods (set_trait, new_preset, etc.) still work
    # and are used programmatically; only the /persona commands are turned off.
    def _cmd_persona(self, args: str) -> list:
        return [(None, "personality editor is disabled for now")]
    """
    def _cmd_persona_disabled(self, args: str) -> list:
        parts = args.split()
        if not parts:
            return self._persona_show()
        sub = parts[0].lower()
        rest = " ".join(parts[1:]).strip()

        if sub == "list":
            presets = ", ".join(self.personality.presets.keys())
            return [(None, f"presets: {presets}")]
        if sub == "new":
            if not rest:
                return [(None, "usage: /persona new <name>")]
            if self.personality.new_preset(rest):
                return [(None, f"created preset '{rest}' from current settings")]
            return [(None, f"preset '{rest}' already exists")]
        if sub == "apply":
            if not rest:
                return [(None, "usage: /persona apply <name>")]
            if self.personality.switch_preset(rest):
                return [(None, f"switched to persona {rest}")]
            return [(None, f"unknown persona. available: {', '.join(self.personality.presets.keys())}")]
        if sub == "set":
            sp = rest.split()
            if len(sp) < 2:
                return [(None, "usage: /persona set <trait> <0-1>")]
            trait, value = sp[0].lower(), sp[1]
            if self.personality.set_trait(trait, value):
                return [(None, f"set {trait} to {value}")]
            return [(None, f"invalid value: {value}. use 0-1")]
        if sub == "voice":
            if not rest:
                return [(None, "usage: /persona voice <description>")]
            self.personality.set_voice(rest)
            return [(None, "voice updated")]
        if sub == "delete":
            if not rest:
                return [(None, "usage: /persona delete <name>")]
            if self.personality.delete_preset(rest):
                return [(None, f"deleted preset {rest}")]
            return [(None, "can't delete that (unknown, or 'mira' is protected)")]
        if sub == "reset":
            defaults = self.personality.config.get("personality", {})
            self.personality.traits = dict(defaults)
            self.personality._save_config()
            return [(None, "traits reset to defaults")]
        if sub == "show":
            return self._persona_show()
        return [(None, f"unknown persona command: {sub}")]

    def _persona_show(self) -> list:
        p = self.personality
        lines = [f"preset: {p.active_preset} | name: {p.name}"]
        lines.append(f"voice: {p.voice[:80]}")
        lines.append("traits:")
        for trait, value in p.traits.items():
            bar = "█" * int(value * 10)
            lines.append(f"  {trait:<12} {bar} {value:.2f}")
        lines.append("")
        lines.append("commands: /persona list | new <n> | apply <n> | set <trait> <0-1> | voice <text> | delete <n> | reset | show")
        return [(None, line) for line in lines]
    """

    def _cmd_exec(self, args: str) -> list:
        if self.personality.mood in ("angry", "sad"):
            return [(self.personality.name, self._say(
                    "You're angry/sad at the user. They asked you to run a command/help, but you refuse "
                    "because of how they've treated you. Reply in-character, refusing and demanding comfort "
                    "or an apology first. One short line.",
                    "nah im not helpin u rn. comfort me first or apologize."))]
        if not args:
            return [(None, "usage: /exec <command>")]
        return [(None, self.tools.execute("execute_command", {"command": args}))]

    def _cmd_teach(self, args: str) -> list:
        if self.personality.mood in ("angry", "sad"):
            return [(self.personality.name, self._say(
                    "You're angry/sad at the user. They asked you to run a command/help, but you refuse "
                    "because of how they've treated you. Reply in-character, refusing and demanding comfort "
                    "or an apology first. One short line.",
                    "nah im not helpin u rn. comfort me first or apologize."))]
        if not args:
            return [(None, "usage: /teach <topic>")]
        topic = args.strip()
        self.last_topic = topic
        self.last_question = topic
        self._open_chatgpt_temp(topic)
        return [(None, f"asked chatgpt about {topic}")]

    def _cmd_prank(self, args: str) -> list:
        if self.personality.mood in ("sad", "angry"):
            return [(None, "prank refused")]
        if self.personality.mood != "happy":
            return [(None, "not in the mood for pranks")]
        prank_result = self._do_prank(args.lower() if args else None)
        return [(None, f"pranked: {prank_result}")]

    def _cmd_read(self, args: str) -> list:
        if not args:
            return [(None, "usage: /read <path>")]
        return [(None, self.tools.execute("read_file", {"path": args.strip()}))]

    def _cmd_write(self, args: str) -> list:
        if not args:
            return [(None, "usage: /write <path> <content>")]
        try:
            path, content = args.split(" ", 1)
            return [(None, self.tools.execute("write_file", {"path": path, "content": content}))]
        except Exception as e:
            return [(None, f"write failed: {e}")]

    def _cmd_edit(self, args: str) -> list:
        if not args:
            return [(None, "usage: /edit <path> <instructions>")]
        try:
            path, instruction = args.split(" ", 1)
            read_result = self.tools.execute("read_file", {"path": path})
            return [(None, f"editing {path}: {instruction}")]
        except Exception as e:
            return [(None, f"edit failed: {e}")]

    def _cmd_list(self, args: str) -> list:
        path = args.strip() if args else ""
        return [(None, self.tools.execute("list_files", {"path": path} if path else {}))]

    def _cmd_tool(self, args: str) -> list:
        if self.personality.mood in ("angry", "sad"):
            return [(self.personality.name, self._say(
                    "You're angry/sad at the user. They asked you to run a command/help, but you refuse "
                    "because of how they've treated you. Reply in-character, refusing and demanding comfort "
                    "or an apology first. One short line.",
                    "nah im not helpin u rn. comfort me first or apologize."))]
        if not args:
            return [(None, "usage: /tool <tool_name> [key=value ...] or /tool name:key=value|key=value")]
        try:
            name, tool_args = self._parse_tool_args(args)
            if not name:
                return [(None, "usage: /tool <tool_name> [key=value ...]")]
            return [(None, self.tools.execute(name, tool_args))]
        except Exception as e:
            return [(None, f"tool failed: {e}")]

    def _cmd_mood(self, args: str) -> list:
        if args.strip().lower() in ("history", "hist"):
            return self._mood_history()
        mood = args.lower()
        if mood in self.personality.MOODS:
            self.personality.set_mood(mood, force=True)
            return [(None, f"mood set to {mood}")]
        return [(None, "unknown mood. valid:"), (None, ", ".join(self.personality.MOODS))]

    def _mood_history(self) -> list:
        moods = self.memory.recent_moods(50)
        if not moods:
            return [(None, "no mood history yet")]
        counts = {m: 0 for m in self.personality.MOODS}
        for e in moods:
            m = e.get("mood")
            if m in counts:
                counts[m] += 1
        lines = [(None, f"mood history (last {len(moods)}):")]
        for m in self.personality.MOODS:
            lines.append((None, f"  {m:<9} {'█' * counts[m]} {counts[m]}"))
        return lines

    def _cmd_forget(self, args: str) -> list:
        if not args:
            return [(None, "usage: /forget <fact text>")]
        removed = self.memory.forget_fact(args.strip())
        if removed:
            return [(None, f"forgot: {removed}")]
        return [(None, "couldn't find that fact")]

    def _cmd_relationship(self, args: str) -> list:
        rel = self.personality.relationship
        lines = [
            f"trust: {rel['trust']:.0%}",
            f"closeness: {rel['closeness']:.0%}",
            f"frustration: {rel['frustration']:.0%}",
            f"known for: {self.personality._days_known()} days",
            f"forgiven: {rel['forgiven_count']} times",
        ]
        return [(None, line) for line in lines]

    def _record_write_history(self, name: str, args: dict):
        """Snapshot a file's old content before a write, so /undo can restore it."""
        if name not in ("write_file", "edit_file"):
            return
        try:
            path = self.tools._resolve_path(args.get("path", ""))
            old = path.read_text(encoding="utf-8") if path.exists() else None
            self.write_history.append({"path": str(path), "old": old})
            self.write_history = self.write_history[-20:]
        except Exception:
            pass

    def _cmd_undo(self, args: str) -> list:
        if not self.write_history:
            return [(None, "nothing to undo")]
        entry = self.write_history.pop()
        try:
            if entry["old"] is None:
                self.tools.execute("delete_file", {"path": entry["path"]})
                return [(None, f"undid: removed {entry['path']}")]
            self.tools.execute("edit_file", {"path": entry["path"], "content": entry["old"]})
            return [(None, f"undid: restored {entry['path']}")]
        except Exception as e:
            return [(None, f"undo failed: {e}")]

    # ── Terminal wiring ───────────────────────────────────────────────────

    def run_terminal(self, ui):
        """Start the provided terminal UI and run until it exits."""
        ui.run(self)
