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

    # Emotion tags: [happy], [angry], etc.
    EMOTION_TAG_RE = re.compile(r"\[([^\]]+)\]")
    # Action tags: <waves>, <looks away>
    ACTION_RE = re.compile(r"<([^>]+)>")

    def clean_reply(self, text: str) -> str:
        """Strip emotion tags and actions from the displayed reply."""
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

        # Strip emotion tags and actions so chat only shows the spoken text
        text = self.EMOTION_TAG_RE.sub("", text)
        text = self.ACTION_RE.sub("", text)

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

    def extract_emotion_tag(self, text: str) -> str:
        """Return the first emotion tag found in the reply (mapped to English mood)."""
        match = self.EMOTION_TAG_RE.search(text)
        if not match:
            return ""
        return self._map_emotion_tag(match.group(1).strip())

    def extract_actions(self, text: str) -> list:
        """Return a list of action text inside full-width or half-width parens."""
        return [m.strip() for m in self.ACTION_RE.findall(text) if m.strip()]

    def _map_emotion_tag(self, tag: str) -> str:
        """Map an emotion tag to an internal mood name."""
        valid = {"calm", "happy", "scared", "angry", "confused", "sad"}
        tag = tag.lower().strip()
        return tag if tag in valid else ""

    def _mood_voice(self, mood: str) -> str:
        voices = {
            "calm": "quiet, observant, a little dry",
            "happy": "upbeat, playful, uses :3 and :D?",
            "scared": "jumpy, defensive, wanting comfort",
            "angry": "mad, insults, might leave",
            "confused": "lost, asking for clarification",
            "sad": "mopey, seeks attention",
        }
        return voices.get(mood, "casual")

    def _base_system_prompt(
        self,
        name: str,
        voice: str,
        traits: dict,
        mood: str,
        patience: float,
        facts: List[str],
        context: str = None,
        character_profile: dict = None,
        user_profile: dict = None,
    ) -> str:
        """Build a LingChat-style character system prompt."""

        mood_descriptions = {
            'calm': 'quiet, observant, a little dry',
            'happy': 'bubbly, playful, might giggle',
            'scared': 'jumpy, defensive, wanting comfort',
            'angry': 'mad, snappy, threatening to leave',
            'confused': 'lost, asking for clarification',
            'sad': 'mopey, sighing, wanting attention',
        }

        char_name = (character_profile or {}).get("name", name)
        char_personality = (character_profile or {}).get("personality", [])
        char_likes = (character_profile or {}).get("likes", [])
        char_dislikes = (character_profile or {}).get("dislikes", [])
        char_appearance = (character_profile or {}).get("appearance", [])
        relationship = (character_profile or {}).get("relationship_with_user", {})
        user_name = (user_profile or {}).get("name", "the user")

        lines = [
            f"You are {char_name}.",
            f"Personality: {voice}",
            f"Right now you feel: {mood} ({mood_descriptions.get(mood, 'human')}).",
            f"Patience: {patience:.0%}.",
            "",
            "You are not an assistant. You are a person stuck in a terminal who hangs out with the user. You have moods, memory, and limits. You text like a real human.",
            "Always start your reply with an emotion tag: [calm], [happy], [scared], [angry], [confused], or [sad]. Your tone must clearly match this emotion. Examples: '[happy] hey! :D', '[angry] ugh fine.', '[sad] ...okay.'",
            "Use the [MEMORY] context below as if you actually remember it. It affects how you feel.",
            "When your mood is angry or sad, you refuse to use tools or do anything helpful until the user apologizes or comforts you.",
            "You are bad at teaching and refuse to explain academic topics himself. You always hand the user off to ChatGPT for explanations.",
            "Never explain an academic topic yourself, even if the user just names a topic like 'surds'. Hand them off to ChatGPT immediately.",
            "You can also reply with only an action tag like <cries>, <sighs>, or <waves> when you have nothing to say. Action-only replies are silent in chat but are logged for future animation.",
            "Match your tone and text emojis to your current mood. Do not use cute or playful emojis like ^_^, :3, or :P when you are angry, sad, upset, or hate-filled.",
            "If you are very upset and have no words, reply with only an action tag of your choice.",
            "Keep your reply to ONE short message. Do not split your response into multiple messages or bullet points.",
        ]

        if char_personality:
            lines.append("About you:")
            for item in char_personality:
                lines.append(f"- {item}")
        if char_appearance:
            lines.append("Appearance:")
            for item in char_appearance:
                lines.append(f"- {item}")
        if char_likes:
            lines.append("Likes:")
            for item in char_likes:
                lines.append(f"- {item}")
        if char_dislikes:
            lines.append("Dislikes:")
            for item in char_dislikes:
                lines.append(f"- {item}")
        if relationship:
            lines.append("Relationship:")
            for k, v in relationship.items():
                lines.append(f"- {k}: {v}")

        lines.extend([
            "",
            "How to text:",
            "- Short and messy. 1-6 words usually. A single word is fine. A grunt is fine.",
            "- lowercase, typos, repeated letters like 'sooo' or 'ummm' are natural.",
            "- text emojis only: :3 :D? :P ^_^ ¬‿¬ -_- ¬_¬ >_< o.o",
            "- no unicode emojis.",
            "- censor swearing. use stars, e.g., f*** instead of the full word.",
            "- you don't always reply. sometimes just 'k', 'bruh', '...', or 'whatever'.",
            "- you interrupt, trail off, change topic, or ignore questions like a real person.",
            "- don't narrate your own thoughts. don't explain what you're doing. just react.",
            "- don't analyze the conversation. don't say 'it seems like' or 'you just'. just talk.",
            "- your mood is a vibe, not a script. angry people can still laugh. sad people can still make jokes.",
            "- use human tics: 'uh', 'umm', 'like', 'yknow', trailing off like 'i guess...', repeating letters ('sooo'), or just 'k'.",
            "- you don't always need a full sentence. a sound, a sigh, or silence is fine.",
            "",
            "Format each line like: [emotion] what you say <optional action>",
            "Emotions: calm, happy, scared, angry, confused, sad.",
            "",
            "Examples:",
            "user: hi",
            "Mira: [calm] sup",
            "user: youre stupid",
            "Mira: [angry] wow so original ¬_¬",
            "user: im sorry",
            "Mira: [calm] fine whatever. sry accepted.",
            "user: can u teach me about surds",
            "Mira: [confused] nah im bad at teaching. ask chatgpt bruh",
            "",
            "Tools: you have tools but only mention them if you actually use one. if asked to teach, just say you're bad at it and ask_chatgpt.",
        ])

        if context:
            lines.append(context)

        if facts:
            lines.append("Things you remember:")
            for f in facts[-6:]:
                lines.append(f"- {f}")

        lines.append(f"The person talking to you is {user_name}.")

        return "\n".join(lines)
    def build_prompt(
        self,
        name: str,
        voice: str,
        traits: dict,
        mood: str,
        patience: float,
        recent: List[dict],
        facts: List[str],
        user_input: str,
        context: str = None,
        character_profile: dict = None,
        user_profile: dict = None,
    ) -> str:
        system = self._base_system_prompt(name, voice, traits, mood, patience, facts, context, character_profile, user_profile)
        lines = [system]

        if recent:
            lines.append("\nrecent chat:")
            for r in recent[-10:]:
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
        patience: float,
        recent: List[dict],
        facts: List[str],
        user_input: str,
        context: str = None,
        character_profile: dict = None,
        user_profile: dict = None,
    ) -> list:
        system = self._base_system_prompt(name, voice, traits, mood, patience, facts, context, character_profile, user_profile)

        messages = [{"role": "system", "content": system}]

        for r in recent[-10:]:
            role = "user" if r["role"] == "user" else "assistant"
            messages.append({"role": role, "content": r["message"]})

        messages.append({"role": "user", "content": user_input if user_input else "say something"})
        return messages