#!/usr/bin/env python3
import json
import os
import random
import re
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
from tools import Tools
from ui import MiraRenderer


def load_config(path: str = "config.json") -> dict:
    return json.loads(Path(path).read_text())


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

        # Shutdown state
        self.exit_message = None

        # Last saved file for "edit that" references
        self.last_saved_path = None

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

    def build_prompt(self, user_input: str, context: str = None) -> str:
        state = self.personality.state()
        recent = self.memory.recent_interactions(5, session_id=self.session_id)
        facts = self.memory.facts_about("user") + self.memory.facts_about("mira")
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
        facts = self.memory.facts_about("user") + self.memory.facts_about("mira")
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

    def _is_edit_request(self, user_input: str) -> bool:
        lower = user_input.lower()
        return any(p in lower for p in ["edit that", "edit this", "edit the document", "edit the file", "edit it", "update that", "change that"])

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
                    self.last_saved_path = str(path)
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
                    self.last_saved_path = str(path)
                    self.memory.remember_fact("mira", f"saved {kind} file to {path}")
                    return f"{kind} saved to {path}"
            except Exception as e:
                return f"couldn't write {kind}: {e}"

        return f"couldn't write {kind}: no safe write location found"

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

    def _detect_insult(self, user_input: str) -> float:
        lower = user_input.lower()
        directed_at_mira = any(p in lower for p in ["you ", "u ", "mira", "your ", "ur "])
        rude_words = [
            "stupid", "idiot", "dumb", "freak", "jerk", "weirdo", "moron", "loser",
            "shut up", "gtfo", "worthless", "fuck", "fuck off", "bitch", "asshole", "dickhead"
        ]
        hits = sum(1 for w in rude_words if w in lower)
        if hits == 0:
            return 0.0
        amount = min(0.5, hits * 0.15)
        if directed_at_mira:
            amount *= 1.5
        if user_input.isupper() and len(user_input) > 3:
            amount += 0.1
        return min(0.5, amount)

    def respond(self, user_input: str) -> list:
        self.memory.log_interaction("user", user_input, session_id=self.session_id)
        self.save_fact_from_user(user_input)
        annoy_amount = self._detect_insult(user_input)
        self.personality.interact(recover_patience=(annoy_amount == 0))
        self.personality.drain_energy()

        if annoy_amount > 0:
            self.personality.annoy(annoy_amount, "insult")

        output = []

        if self.personality.energy <= 0:
            self._generate_close_message("tired")
            raise RuntimeError("Mira is too tired and shut down")

        close_thr = self.config["patience"]["close_terminal_threshold"]
        if self.personality.mood in ("angry", "annoyed") and self.personality.patience <= close_thr:
            self._generate_close_message("angry")
            raise RuntimeError("Mira got fed up and shut down")

        try:
            messages = self.build_messages(user_input)
            # Append available-tool reminder to system prompt
            messages[0]["content"] += (
                "\n\nAvailable tools: time, write_file, read_file, edit_file, delete_file, list_files, execute_command. "
                "Use write_file/read_file/edit_file for file operations. Never dump long content in chat."
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

            reply = self.llm.clean_reply(final_reply) if final_reply else "done."
        except Exception as e:
            reply = f"(AI failed: {e})"

        if reply:
            output.append((self.personality.name, reply))

        full_reply = "\n".join([line for line in (reply or "").split("\n") if "*" not in line])
        if full_reply:
            self.memory.log_interaction("companion", full_reply, session_id=self.session_id)

        self.personality.update(user_input)
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
        if random.random() < 0.25:
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
        prompts = {"angry": "im leaving", "sad": "im tired", "tired": "im too tired"}
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
        """Handle 'edit that' / 'edit the file' by rewriting the last saved file."""
        from pathlib import Path as _Path

        if not self.last_saved_path:
            return [(self.personality.name, "no file has been saved yet to edit")]
        path = _Path(self.last_saved_path)
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
        """Parse /tool argument strings.

        Supports forms like:
            /tool time
            /tool write_file path=hi.txt content=hello world
            /tool write_file:path=hi.txt|content=hello world
        """
        # Normalize pipe separators to spaces so the same parser handles both
        normalized = raw.replace("|", " ")
        parts = normalized.split()
        if not parts:
            return None, {}

        name = parts[0]
        # handle /tool write_file:path=hi.txt
        if ":" in name:
            name, first_arg = name.split(":", 1)
            parts = [first_arg] + parts[1:]
        else:
            parts = parts[1:]

        tool_args = {}
        current_key = None
        for token in parts:
            if "=" in token:
                key, value = token.split("=", 1)
                current_key = key
                tool_args[key] = value
            elif current_key is not None:
                tool_args[current_key] += " " + token
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
                "  /time           show current time",
                "  /tool <args>    run a tool manually",
                "  /mood <mood>    force a mood",
                "  /kill           end session",
                "  /help           show this help",
                "",
                "tools:",
                "  time            get current time",
                "  write_file      create or overwrite a file",
                "  read_file       read a file",
                "  edit_file       overwrite an existing file",
                "  delete_file     delete a file",
                "  list_files      list files in a directory",
                "  execute_command run a shell command",
                "",
                "examples:",
                "  /tool time",
                "  /tool write_file path=hi.txt content=hello world",
                "  /tool read_file path=hi.txt",
                "  /tool edit_file path=hi.txt content=goodbye world",
                "  /tool delete_file path=hi.txt",
                "  /tool list_files path=.",
                "  /tool execute_command command=ls -la",
            ]
            for line in help_lines:
                self._chat_message(None, line)
            return True

        if cmd == "/time":
            result = self.tools.execute("time", {})
            self._chat_message(None, result)
            return True

        if cmd == "/kill":
            self._chat_message(None, "ending session")
            self.running = False
            return True

        if cmd in ("/tool", "/tools"):
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
                self._chat_message(None, f"unknown mood. valid: {', '.join(self.personality.MOODS)}")
            return True

        self._chat_message(None, f"unknown command: {cmd}")
        return True

    # ── UI helpers ───────────────────────────────────────────────────────────

    def _chat_message(self, sender, text):
        if not text:
            return
        for sub in str(text).split("\n"):
            sub = sub.strip()
            if not sub:
                continue
            if sender == self.personality.name:
                self.chat_lines.append((sender, sub, self.personality.mood))
            else:
                self.chat_lines.append((sender, sub))

    def _ui_state(self):
        mode = self.typing_state if self.typing_state else "idle"
        return {
            "mood": self.personality.mood,
            "energy": self.personality.energy,
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
                user_input = self.user_queue.get(timeout=0.5)
            except Empty:
                continue
            self.last_user_time = time.time()

            self.typing_state = "typing"
            time.sleep(random.uniform(1, 2))

            self.typing_state = "thinking"
            try:
                messages = self.respond(user_input)
            except RuntimeError:
                self.mira_queue.put((None, "_close_terminal"))
                return
            except Exception as e:
                self.mira_queue.put((self.personality.name, f"(AI failed: {e})"))
                self.typing_state = None
                continue

            reply_text = " ".join([text for _, text in messages if _ == self.personality.name])
            word_count = len(reply_text.split())
            delay = min(10, max(3, word_count * 0.5 + random.uniform(0, 2)))

            self.typing_state = "typing"
            start = time.time()
            while time.time() - start < delay:
                if not self.running:
                    return
                time.sleep(0.1)

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

            self.personality.recover_energy_idle(0.01)

            elapsed = time.time() - self.last_user_time
            if int(elapsed) % 60 != 0:
                continue

            if elapsed > 600 and random.random() < 0.05:
                self.personality.annoy(0.03, "ghosted")

            if self.personality.mood == "angry" and elapsed > 600:
                if random.random() < 0.3:
                    self.mira_queue.put((self.personality.name, random.choice([
                        "u ghosted me... fine. im out.", "im done waiting"
                    ])))
                    self.mira_queue.put((None, "_close_terminal"))
                    return

            if self.personality.mood == "angry" and elapsed > 180:
                if random.random() < 0.1:
                    self.mira_queue.put((self.personality.name, random.choice([
                        "wow ok ignore me", ":/", "u suck", "im done waiting"
                    ])))
            elif elapsed > 1200:
                if random.random() < 0.1:
                    self.mira_queue.put((self.personality.name, random.choice([
                        "u still there?", "hello?", "...", "u alive?"
                    ])))

    def _close_terminal(self):
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

                if ch in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT,
                          curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END,
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

                            if user_input.startswith("/"):
                                if self._handle_slash_command(user_input):
                                    continue

                            self._chat_message("You", user_input)
                            self.pending_batch.append(user_input)
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
                print(f"\n{state['mood']} | energy {state['energy']:.0%} | patience {state['patience']:.0%} | {datetime.now().strftime('%H:%M')}")
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
    main()
