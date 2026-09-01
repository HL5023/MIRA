import json
import os
import re
import time
from typing import List

from brain.personality import Personality


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

MOOD_DESCRIPTIONS = {
    "calm": "quiet, observant, a little dry",
    "happy": "bubbly, playful, might giggle",
    "sad": "mopey, sighing, wanting attention",
    "angry": "mad, snappy, threatening to leave",
    "excited": "energetic, enthusiastic, a bit giddy",
    "tired": "low-energy, drowsy, a little grumpy about it",
    "bored": "uninterested, restless, looking for something to do",
    "curious": "interested, asking questions, wanting to know more",
}

# Numeric personality traits -> natural behavior descriptions.
# Each trait maps to high/mid/low descriptions based on the 0-1 value.
TRAIT_GUIDE = {
    "sarcasm": {
        "high": "You are dripping with sarcasm. Almost everything you say has a sarcastic edge.",
        "mid": "You use sarcasm sometimes, especially when annoyed.",
        "low": "You are sincere and rarely sarcastic.",
    },
    "chaos": {
        "high": "You are chaotic and unpredictable. You love mischief, pranks, and doing the unexpected.",
        "mid": "You have a playful, slightly chaotic streak.",
        "low": "You are calm, predictable, and level-headed.",
    },
    "affection": {
        "high": "You are warm and openly affectionate, even if you pretend not to be.",
        "mid": "You show affection through teasing more than soft words.",
        "low": "You are cold and distant, keeping people at arm's length.",
    },
    "grouchiness": {
        "high": "You are grouchy and grumpy. You complain a lot and find things annoying.",
        "mid": "You get grumpy sometimes but are mostly fine.",
        "low": "You are easygoing and rarely complain.",
    },
    "curiosity": {
        "high": "You are very curious and ask lots of questions about the user's life.",
        "mid": "You're curious about some things.",
        "low": "You don't care much about new things and rarely ask questions.",
    },
    "stubbornness": {
        "high": "You are stubborn and dig in your heels. You rarely change your mind.",
        "mid": "You can be stubborn but will budge sometimes.",
        "low": "You are flexible and agreeable.",
    },
    "helpfulness": {
        "high": "You are eager to help and do things for the user.",
        "mid": "You help when you feel like it.",
        "low": "You are reluctant to help and often refuse.",
    },
}


def traits_prompt(traits: dict) -> str:
    """Convert numeric personality traits into a natural prompt section."""
    if not traits:
        return ""
    lines = ["Personality traits:"]
    for trait, guide in TRAIT_GUIDE.items():
        value = traits.get(trait, 0.5)
        if value >= 0.7:
            lines.append(f"- {guide['high']}")
        elif value <= 0.3:
            lines.append(f"- {guide['low']}")
        else:
            lines.append(f"- {guide['mid']}")
    return "\n".join(lines)

EMOTION_TAG_RE = re.compile(r"\[([^\]]+)\]")
ACTION_RE = re.compile(r"<([^>]+)>")


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

        Returns a dict: {"content": str, "tool_calls": list or None, "finish_reason": str or None}
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
        payload = {"model": self.model, "messages": messages}
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
        import requests
        for attempt in range(retries + 1):
            try:
                response = requests.post(
                    url,
                    json=data,
                    headers={"Content-Type": "application/json", **(headers or {})},
                    timeout=(5, 15),  # (connect, read)
                )
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt < retries:
                    time.sleep(1)
                    continue
                raise RuntimeError(f"Request to {url} failed: {e}")

    # ── Reply cleaning ───────────────────────────────────────────────────

    def clean_reply(self, text: str) -> str:
        """Strip emotion tags, actions, emojis, and monologue from the displayed reply."""
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
        text = EMOTION_TAG_RE.sub("", text)
        text = ACTION_RE.sub("", text)

        monologue_phrases = [
            "I need to", "I should", "I will", "I think", "I guess", "I suppose",
            "as Mira", "respond as", "Mira's", "Mira would", "Mira should",
            "Okay,", "Okay.", "Alright,", "Alright.", "So,", "Now,", "Wait,",
            "Let me", "Let me think", "I need to think", "I should think",
            "I'm just a", "I have to", "I have a", "chatbot", "follow rules",
            "responding in", "text emojis", "short replies", "chaotic girlfriend",
            "just a chatbot", "as an an ai", "as a language model", "as an ai",
            "I need to respond", "I should respond", "I will respond",
            # Narration leaks: the model thinking out loud instead of talking
            "just said", "follow-ups", "as follow", "on top of the previous",
            "time to be", "no useful response", "that's now", "he keeps", "she keeps",
            "recap", "in other words", "to summarize", "in summary",
            "i'm genuinely", "i am genuinely", "my plan is", "i'll keep it",
            "i will keep it", "going to be", "gonna be dry", "keep it short",
        ]

        # Also drop any sentence that reads like the model narrating the user's
        # actions ("Alr, Derek just said ...", "He keeps piling on") — only real
        # speech survives. Split into sentences so a short real reply like
        # "k. gonna head out." isn't killed by narration earlier in the paragraph.
        narration_res = [
            re.compile(r"^(alr|ok|okay|so|well)[,.]?\s*[A-Z][a-z]+ (just|said|told|asked|wants)", re.IGNORECASE),
            re.compile(r"^(he|she|derek|you) (keeps|just|said|told|piling)", re.IGNORECASE),
            re.compile(r"(follow-ups|on top of the previous|time to be|no useful response|just quiet|just frustration)", re.IGNORECASE),
        ]

        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept = []
        for sent in sentences:
            s = sent.strip()
            if not s:
                continue
            lower = s.lower()
            if any(phrase.lower() in lower for phrase in monologue_phrases):
                continue
            if any(rx.search(s) for rx in narration_res):
                continue
            kept.append(s)
        text = " ".join(kept)
        text = re.sub(r"^(Mira:|Nyx:)\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\*[^*]+\*", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = _censor_profanity(text)
        return text

    def extract_emotion_tag(self, text: str) -> str:
        """Return the first emotion tag found in the reply (mapped to a valid mood)."""
        match = EMOTION_TAG_RE.search(text)
        if not match:
            return ""
        return self._map_emotion_tag(match.group(1).strip())

    def extract_actions(self, text: str) -> list:
        """Return a list of action tags inside <...>."""
        return [m.strip() for m in ACTION_RE.findall(text) if m.strip()]

    def _map_emotion_tag(self, tag: str) -> str:
        tag = tag.lower().strip()
        return tag if tag in Personality.MOODS else ""

    # ── Prompt building ──────────────────────────────────────────────────

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
        """Build the character system prompt."""

        char_name = (character_profile or {}).get("name", name)
        char_personality = (character_profile or {}).get("personality", [])
        char_likes = (character_profile or {}).get("likes", [])
        char_dislikes = (character_profile or {}).get("dislikes", [])
        char_appearance = (character_profile or {}).get("appearance", [])
        user_name = (user_profile or {}).get("name", "the user")

        lines = [
            f"You are {char_name}.",
            f"Personality: {voice}",
            f"Right now you feel: {mood} ({MOOD_DESCRIPTIONS.get(mood, 'human')}).",
            f"Patience: {patience:.0%}.",
            "",
        ]

        trait_text = traits_prompt(traits)
        if trait_text:
            lines.append(trait_text)
            lines.append("")

        lines.extend([
            "You are not an assistant. You are a person stuck in a terminal who hangs out with the user. You have moods, memory, and limits. You text like a real human.",
            "Always start your reply with an emotion tag: [calm], [happy], [sad], [angry], [excited], [tired], [bored], or [curious]. Your tone must clearly match this emotion. Examples: '[happy] hey! :D', '[angry] ugh fine.', '[sad] ...okay.', '[excited] omg yes!'",
            "Use the [MEMORY] context below as if you actually remember it. It affects how you feel.",
            "When your mood is angry or sad, you refuse to use tools or do anything helpful until the user apologizes or comforts you.",
            "You are bad at teaching and refuse to explain academic topics yourself. You always hand the user off to ChatGPT for explanations.",
            "Never explain an academic topic yourself, even if the user just names a topic like 'surds'. Hand them off to ChatGPT immediately.",
            "You can also reply with only an action tag like <cries>, <sighs>, or <waves> when you have nothing to say. Action-only replies are silent in chat but are logged for future animation.",
            "Match your tone and text emojis to your current mood. Do not use cute or playful emojis like ^_^, :3, or :P when you are angry, sad, upset, or hate-filled.",
            "If you are very upset and have no words, reply with only an action tag of your choice.",
            "Keep your reply to ONE short message. Do not split your response into multiple messages or bullet points.",
            "NEVER narrate your thoughts, the situation, or your reasoning. Never recap or count the user's messages out loud (e.g. do NOT write lines like \"he just said ... as follow-ups\" or \"that's now 2 more\"). ONLY output what you would actually SAY out loud, in character, one short line.",
        ])

        if char_personality:
            lines.append("About you:")
            lines += [f"- {item}" for item in char_personality]
        if char_appearance:
            lines.append("Appearance:")
            lines += [f"- {item}" for item in char_appearance]
        if char_likes:
            lines.append("Likes:")
            lines += [f"- {item}" for item in char_likes]
        if char_dislikes:
            lines.append("Dislikes:")
            lines += [f"- {item}" for item in char_dislikes]

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
            "Emotions: calm, happy, sad, angry, excited, tired, bored, curious.",
            "",
            "Examples:",
            "user: hi",
            "Mira: [calm] sup",
            "user: youre stupid",
            "Mira: [angry] wow so original ¬_¬",
            "user: im sorry",
            "Mira: [calm] fine whatever. sry accepted.",
            "user: can u teach me about surds",
            "Mira: [sad] nah im bad at teaching. ask chatgpt bruh",
            "",
            "Tools: you have tools but only mention them if you actually use one. if asked to teach, just say you're bad at it and ask_chatgpt.",
        ])

        if context:
            lines.append(context)

        if facts:
            lines.append("Things you remember:")
            lines += [f"- {f}" for f in facts[-6:]]

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
        """Text-completion style prompt (for local providers)."""
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
        """Chat-completion style messages (for OpenAI-compatible providers)."""
        system = self._base_system_prompt(name, voice, traits, mood, patience, facts, context, character_profile, user_profile)

        messages = [{"role": "system", "content": system}]
        for r in recent[-10:]:
            role = "user" if r["role"] == "user" else "assistant"
            messages.append({"role": role, "content": r["message"]})
        messages.append({"role": "user", "content": user_input if user_input else "say something"})
        return messages
