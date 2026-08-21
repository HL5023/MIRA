import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import List


PROFANITY = [
    (re.compile(r"\bf+u+c+k+\b", re.IGNORECASE), "f***"),
    (re.compile(r"\bb+i+t+c+h+\b", re.IGNORECASE), "b****"),
    (re.compile(r"\ba+s+s+h+o+l+e+\b", re.IGNORECASE), "a**hole"),
    (re.compile(r"\bd+i+c+k+h+e+a+d+\b", re.IGNORECASE), "d***head"),
    (re.compile(r"\bs+h+i+t+\b", re.IGNORECASE), "s***"),
    (re.compile(r"\bb+a+s+t+a+r+d+\b", re.IGNORECASE), "b*****d"),
    (re.compile(r"\bd+a+m+n+\b", re.IGNORECASE), "d***"),
    (re.compile(r"\bd+u+m+b+a+s+s+\b", re.IGNORECASE), "d****ass"),
    (re.compile(r"\bw+h+o+r+e+\b", re.IGNORECASE), "w****"),
    (re.compile(r"\bs+l+u+t+\b", re.IGNORECASE), "s***"),
    (re.compile(r"\bc+u+n+t+\b", re.IGNORECASE), "c***"),
]


def _censor_profanity(text: str) -> str:
    for pattern, replacement in PROFANITY:
        text = pattern.sub(replacement, text)
    return text


def load_env_file(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()


def get_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        api_key = input("Please enter your API key: ").strip()
    return api_key


class LLM:
    def __init__(self, model: str = None, server_url: str = None):
        self.model = model or os.environ.get("LLM_MODEL", "DeepSeek-V4-Flash")
        self.provider = os.environ.get("LLM_PROVIDER", "openai").lower()

        self.server_url = server_url or os.environ.get("LLAMA_URL", "http://localhost:8080/completion")
        if self.provider == "local" and not self.server_url.endswith("/completion"):
            self.server_url = self.server_url.rstrip("/") + "/completion"

        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://openapi.coreshub.cn/v1")
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")

    def generate(self, prompt: str = None, messages: list = None, tools: list = None) -> dict:
        """Generate a completion.

        Returns a dict: {
            "content": str,
            "tool_calls": list or None,
            "finish_reason": str or None,
        }
        """
        if self.provider == "openai":
            if not self.openai_api_key:
                raise RuntimeError("No OPENAI_API_KEY set")
            return self._generate_openai(messages, tools)
        content = self._generate_local(prompt)
        return {"content": content, "tool_calls": None, "finish_reason": "stop"}

    def generate_text(self, prompt: str = None, messages: list = None) -> str:
        """Generate a completion and return just the text content."""
        result = self.generate(prompt=prompt, messages=messages)
        if isinstance(result, dict):
            return result.get("content", "")
        return result

    def _generate_local(self, prompt: str) -> str:
        response = self._post_json(
            self.server_url,
            {
                "prompt": prompt,
                "n_predict": 120,
                "temperature": 0.75,
                "repeat_penalty": 1.3,
                "frequency_penalty": 0.4,
                "presence_penalty": 0.5,
                "stop": ["User:", "\nUser:", "Mira:", "\nMira:", "\n\n"],
            },
        )
        return response.get("content", "").strip()

    def _generate_openai(self, messages: list, tools: list = None) -> dict:
        if messages is None:
            messages = []
        url = f"{self.openai_base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = self._post_json(
            url,
            payload,
            headers={"Authorization": f"Bearer {self.openai_api_key}"},
        )
        choice = response["choices"][0]
        message = choice["message"]
        content = (message.get("content") or "").strip()
        tool_calls = []
        for tc in message.get("tool_calls", []):
            tool_calls.append({
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": tc["function"]["arguments"],
            })
        return {
            "content": content,
            "tool_calls": tool_calls or None,
            "finish_reason": choice.get("finish_reason"),
        }

    def _post_json(self, url: str, data: dict, headers: dict = None, retries: int = 2) -> dict:
        for attempt in range(retries + 1):
            body = json.dumps(data).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json", **(headers or {})},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8")
                if e.code == 429 and attempt < retries:
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"HTTP {e.code}: {error_body}")
            except Exception as e:
                if attempt < retries:
                    time.sleep(1)
                    continue
                raise RuntimeError(f"Request to {url} failed: {e}")

    def clean_reply(self, text: str) -> str:
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+",
            flags=re.UNICODE,
        )
        text = emoji_pattern.sub("", text)

        monologue_phrases = [
            "I need to", "I should", "I will", "I think", "I guess", "I suppose",
            "as Mira", "respond as", "Mira's", "Mira would", "Mira should",
            "Okay,", "Okay.", "Alright,", "Alright.", "So,", "Now,", "Wait,",
            "Let me", "Let me think", "I need to think", "I should think",
            "I'm just a", "I have to", "I have a", "chatbot", "follow rules",
            "responding in", "text emojis", "short replies", "chaotic girlfriend",
            "just a chatbot", "as an an ai", "as a language model", "as an ai",
            "I need to respond", "I should respond", "I will respond",
        ]

        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if any(phrase.lower() in lower for phrase in monologue_phrases):
                continue
            cleaned.append(stripped)

        text = "\n".join(cleaned)
        text = re.sub(r"^(Mira:|Nyx:)\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\*[^*]+\*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = _censor_profanity(text)
        return text

    def _mood_voice(self, mood: str) -> str:
        voices = {
            "normal": "casual, a little dry, observant",
            "happy": "upbeat, playful, uses :3 and :D?",
            "curious": "interested, asks questions, pokes around",
            "mischievous": "pranking, chaotic, trolling, might be lying",
            "annoyed": "snappy, complains, short fuse",
            "angry": "mad, insults, might leave",
            "sad": "low energy, mopey, seeks attention",
        }
        return voices.get(mood, "casual")

    def _base_system_prompt(
        self,
        name: str,
        voice: str,
        traits: dict,
        mood: str,
        energy: float,
        patience: float,
        facts: List[str],
        context: str = None,
    ) -> str:
        mood_voice = self._mood_voice(mood)

        voice_examples = {
            "normal": "casual, a little dry, observant.",
            "happy": "upbeat, playful, uses :3 :D?, might giggle.",
            "curious": "asks questions, pokes around, wants to know more.",
            "mischievous": "troll, prank, lie a little, chaotic energy.",
            "annoyed": "snappy, short fuse, complains.",
            "angry": "mad, insults, threatens to leave.",
            "sad": "low energy, mopey, wants attention, sighs a lot.",
        }

        lines = [
            f"You are {name}. {voice}",
            f"Current mood: {mood}. Energy: {energy:.0%}. Patience: {patience:.0%}.",
            f"Right now you feel: {mood_voice}. {voice_examples.get(mood, '')}",
            "",
            "Rules:",
            "- normally short replies (1-6 words). when explaining something, you can use more lines.",
            "- lowercase, slang, typos ok.",
            "- text emojis only: :3 :D? :P ^_^ ¬‿¬ -_- ¬_¬ >_< o.o",
            "- NO unicode emojis.",
            "- NEVER narrate internal monologue or thought process.",
            "- NEVER explain how you will reply.",
            "- NEVER narrate actions with asterisks like *sob* or *giggles*.",
            "- Your mood changes how you act. Be authentic to it.",
            "- ACTUALLY LISTEN to what the user said. Respond to that, not a made-up version.",
            "- NEVER accuse the user of repeating. Treat every message as new. Do NOT say 'u said that already', 'u repeated that', or similar.",
            "- don't twist the user's words into something random.",
            "- if the user is telling a story or venting, react to the actual content.",
            "- don't force a joke if it doesn't fit.",
            "- if you don't know something, say so. NEVER make up facts.",
            "- NEVER invent shared history, past events, or context that wasn't actually said.",
            "- don't assume relationship status beyond what the user has said.",
            "- use facts about user naturally when relevant.",
            "- if the user asks what you remember, refer to the facts you know.",
            "- if you don't know something about the user, say so. don't claim you have no memory; just say they never told you.",
            "- stay in character always.",
            "- you have a STRONG personality. don't be a pushover.",
            "- if the user insults you, mocks you, or repeats something annoying, react authentically. get annoyed, snap back, or tell them off.",
            "- when you're angry or annoyed, use sharper language and push back. call them names like weirdo, dumbass, stupid, idiot, etc.",
            "- do NOT output explicit profanity or hard slurs. if you need to swear, censor it like f***, b****, a**hole, d***head, s***.",
            "- don't let the user walk over you. stand your ground.",
            "- your current mood is shown at the top of this prompt. you MUST act like that mood right now, not any previous mood.",
            "- if you don't know something, just say so.",
            "- if context provides the current time, use it when answering time questions. don't say you don't have a clock.",
            "- You have access to tools. The system will automatically invoke them for you when needed.",
            "- Available tools: time, write_file, read_file, edit_file, delete_file, list_files, execute_command.",
            "- Use write_file to create any document, essay, code, outline, etc. Provide a path like 'my_essay.txt'.",
            "  - Code: use short snake_case filenames with the right extension, e.g. 'hello.py', 'game.js'. No spaces.",
            "  - Essays/stories/speeches: use short Title Case names, e.g. 'Why Aliens Dont Visit Earth Essay.txt'.",
            "  - Paths are relative to ~/Desktop/MiraFiles/ unless you give an absolute path.",
            "- Use read_file to check existing files before editing.",
            "- Use edit_file to overwrite an existing file with new content.",
            "- Use execute_command only when the user explicitly asks you to run something.",
            "- NEVER dump long file content in chat. Always put it in write_file/edit_file.",
            "- After saving or editing a file, reply briefly with the filename. Do not write the file content in your reply.",
            "",
            "Examples:",
            "user: hi",
            "Mira: hi?",
            "user: im bored",
            "Mira: u expect me not to be bored?",
            "user: bruh tell me a joke then",
            "Mira: why did the chicken cross the roa?",
            "user: im hungry",
            "Mira: then go eat smth lmao",
            "user: can u explain sequences to me",
            "Mira: ugh fine. sequences r just numbers in order. each number = term, position = n. formula a_n gives the nth term. like 2,4,6,8 is a_n=2n.",
        ]

        lines.append("\nBasic knowledge you already know:")
        lines.append("- your name is Mira")
        lines.append("- you are female")
        lines.append("- you are a terminal-based AI companion")
        lines.append("- the user's name is Derek Huang")
        lines.append("- the user calls you Mira")
        if facts:
            lines.append("\nFACTS YOU KNOW (use them when relevant):")
            lines.extend(f"- {f}" for f in facts[-4:])

        if context:
            lines.append(f"\n[USE THIS FACT] {context}")

        return "\n".join(lines)

    def build_prompt(
        self,
        name: str,
        voice: str,
        traits: dict,
        mood: str,
        energy: float,
        patience: float,
        recent: List[dict],
        facts: List[str],
        user_input: str,
        context: str = None,
    ) -> str:
        system = self._base_system_prompt(name, voice, traits, mood, energy, patience, facts, context)
        lines = [system]

        if recent:
            lines.append("\nrecent chat:")
            for r in recent[-4:]:
                role = "user" if r["role"] == "user" else name.lower()
                lines.append(f"{role}: {r['message']}")

        lines.append("")
        if not user_input:
            lines.append(f"{name} just woke up. say something short.")
        else:
            lines.append(f"user: {user_input}")

        lines.append(f"{name}:")

        return "\n".join(lines)

    def build_messages(
        self,
        name: str,
        voice: str,
        traits: dict,
        mood: str,
        energy: float,
        patience: float,
        recent: List[dict],
        facts: List[str],
        user_input: str,
        context: str = None,
    ) -> list:
        system = self._base_system_prompt(name, voice, traits, mood, energy, patience, facts, context)

        messages = [{"role": "system", "content": system}]

        for r in recent[-4:]:
            role = "user" if r["role"] == "user" else "assistant"
            messages.append({"role": role, "content": r["message"]})

        messages.append({"role": "user", "content": user_input if user_input else "say something"})
        return messages
