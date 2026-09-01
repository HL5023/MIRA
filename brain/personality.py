import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from brain.memory import Memory


class Personality:
    """Mira's emotional state: mood selection + a single patience meter."""

    MOODS = ["calm", "happy", "sad", "angry", "excited", "tired", "bored", "curious"]

    MOOD_KEYWORDS: Dict[str, List[str]] = {
        "calm": ["calm", "chill", "relax", "okay", "fine", "alright"],
        "happy": ["happy", "glad", "yay", "nice", "good", "great", "awesome", "lol", ":D", ":3", "^_^"],
        "sad": ["sad", "cry", "depressed", "upset", "lonely", "hurt", "miss", "sorry", ":(", "afraid", "fear"],
        "angry": ["angry", "mad", "pissed", "shut up", "ugh", "hate", "idiot", "stupid", "confused", "dont understand", "lost", "wtf"],
        "excited": ["excited", "hyped", "hype", "omg", "can't wait", "cant wait", "so pumped", "woohoo", "awesome", "amazing"],
        "tired": ["tired", "exhausted", "sleepy", "drained", "worn out", "no energy", "long day", "so sleepy"],
        "bored": ["bored", "boring", "nothing to do", "meh", "whatever", "same old"],
        "curious": ["curious", "wonder", "interesting", "tell me more", "why", "huh", "idk"],
    }

    SENTIMENT_BOOST: Dict[str, List[str]] = {
        "happy": ["good", "great", "awesome", "love", "nice"],
        "sad": ["bad", "terrible", "awful", "cry", "upset", "fear", "scared"],
        "angry": ["hate", "mad", "pissed", "furious", "rage", "confused"],
        "excited": ["excited", "hyped", "hype", "omg", "can't wait"],
        "tired": ["tired", "exhausted", "drained", "sleepy"],
        "bored": ["bored", "boring", "meh", "same"],
        "curious": ["curious", "wonder", "interesting", "why"],
    }

    # Negative/sticky moods resist neutral inputs; stable moods keep Mira from flapping.
    STICKY_NEGATIVE = {"angry", "sad"}

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = json.loads(Path(config_path).read_text())
        self.memory = Memory()

        self.presets = self.config.get("personality_presets", {})
        self.active_preset = self.config.get("active_preset", "mira")
        if not self.presets:
            self.presets = {
                "mira": {
                    "name": self.config.get("name", "Mira"),
                    "voice": self.config.get("voice", ""),
                }
            }
            self.active_preset = "mira"

        self.traits = self.config.get("personality", {})
        self.mood_drift = self.config.get("mood_drift", {})
        self.patience_config = self.config.get("patience", {})
        self.character_profile = self.config.get("character_profile", {})
        self.user_profile = self.config.get("user_profile", {})

        self.mood_confidence_threshold = self.config.get("mood_confidence_threshold", 0.35)

        state = self.memory.get_state()
        saved_mood = state.get("mood", "calm")
        saved_patience = state.get("patience", 1.0)
        self.annoyance_reason = state.get("annoyance_reason", "")
        self._load_relationship(state)

        # Startup recovery: don't instantly re-shutdown from saved anger.
        close_thr = self.patience_config.get("close_terminal_threshold", 0.10)
        self.patience = min(1.0, float(saved_patience) + 0.2)
        if self.patience <= close_thr + 0.05 or saved_mood == "angry":
            self.patience = max(close_thr + 0.15, self.patience)
            self.mood = "angry" if saved_mood == "angry" else (saved_mood if saved_mood in self.MOODS else "calm")
        else:
            self.mood = saved_mood if saved_mood in self.MOODS else "calm"
            # If there was a recent shutdown, don't boot up friendly
            try:
                if any(e.get("event_type") == "shutdown" for e in self.memory.recent_events(10)):
                    self.mood = "angry"
                    self.patience = min(self.patience, 0.35)
            except Exception:
                pass

        self.mood_changed_at = 0.0
        self.mood_cooldown = float(self.config.get("settings", {}).get("mood_cooldown", 30.0))
        self.last_interaction = state.get("last_interaction")

        self.mood_confidence = 1.0
        self.top_k: List[Tuple[str, float]] = []

        self.name = "Mira"
        self.voice = ""
        self._load_preset(self.active_preset)

    # ── Relationship (evolves over time) ─────────────────────────────────

    def _load_relationship(self, state: dict):
        """Load the dynamic relationship, seeded from config on first run."""
        config_rel = self.character_profile.get("relationship_with_user", {})
        saved = state.get("relationship", {})
        self.relationship = {
            "trust": float(saved.get("trust", config_rel.get("trust", 0.6))),
            "closeness": float(saved.get("closeness", 0.0)),
            "frustration": float(saved.get("frustration", config_rel.get("frustration", 0.0))),
            "first_interaction": saved.get("first_interaction"),
            "forgiven_count": int(saved.get("forgiven_count", 0)),
        }
        if not self.relationship["first_interaction"]:
            self.relationship["first_interaction"] = datetime.now().isoformat()

    def adjust_relationship(self, insult: float = 0.0, apology: bool = False, kind: bool = False):
        """Update trust/closeness/frustration based on how the user treats her."""
        rel = self.relationship
        if insult > 0:
            rel["trust"] = max(0.0, rel["trust"] - 0.05 - insult * 0.1)
            rel["frustration"] = min(1.0, rel["frustration"] + 0.1 + insult * 0.2)
        if apology:
            rel["trust"] = min(1.0, rel["trust"] + 0.05)
            rel["frustration"] = max(0.0, rel["frustration"] - 0.15)
            if rel["frustration"] <= 0.2:
                rel["forgiven_count"] += 1
        if kind:
            rel["trust"] = min(1.0, rel["trust"] + 0.005)
            rel["closeness"] = min(1.0, rel["closeness"] + 0.002)
        # Frustration slowly fades with time
        rel["frustration"] = max(0.0, rel["frustration"] - 0.01)

    def relationship_context(self) -> str:
        """Build a prompt snippet describing the current relationship."""
        rel = self.relationship
        parts = []
        days = self._days_known()
        if days:
            parts.append(f"You've known the user for {days} day{'s' if days != 1 else ''}.")
        trust = rel["trust"]
        if trust >= 0.7:
            parts.append("You trust the user deeply and feel close to them. You're warm, open, and tease affectionately.")
        elif trust >= 0.4:
            parts.append("You're getting to know the user. Cautious but friendly.")
        else:
            parts.append("You don't trust the user much right now. You're guarded, cold, and quick to snap.")
        if rel["frustration"] > 0.5:
            parts.append("You're still frustrated with the user and need them to make it up to you.")
        if rel["forgiven_count"]:
            parts.append(f"You've forgiven the user {rel['forgiven_count']} time{'s' if rel['forgiven_count'] != 1 else ''} before.")
        return " ".join(parts)

    def _days_known(self) -> int:
        first = self.relationship.get("first_interaction")
        if not first:
            return 0
        try:
            return (datetime.now() - datetime.fromisoformat(first)).days
        except Exception:
            return 0

    # ── Presets ──────────────────────────────────────────────────────────

    def _load_preset(self, preset_name: str):
        preset = self.presets.get(preset_name, self.presets.get("mira", {}))
        self.name = preset.get("name", self.config.get("name", "Mira"))
        self.voice = preset.get("voice", self.config.get("voice", ""))
        self.traits = dict(preset.get("traits", self.config.get("personality", {})))
        self.active_preset = preset_name

    def switch_preset(self, preset_name: str) -> bool:
        if preset_name not in self.presets:
            return False
        self._load_preset(preset_name)
        return True

    # ── Personality editor ───────────────────────────────────────────────

    def set_trait(self, trait: str, value: float) -> bool:
        """Set a personality trait (0-1). Returns True on success."""
        try:
            value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return False
        value = round(value, 2)
        self.traits[trait] = value
        self.presets.setdefault(self.active_preset, {}).setdefault("traits", {})[trait] = value
        self._save_config()
        return True

    def set_voice(self, text: str):
        self.voice = text
        self.presets.setdefault(self.active_preset, {})["voice"] = text
        self._save_config()

    def new_preset(self, name: str) -> bool:
        """Create a new preset from current settings."""
        if not name or name in self.presets:
            return False
        self.presets[name] = {
            "name": self.name,
            "voice": self.voice,
            "traits": dict(self.traits),
        }
        self._save_config()
        return True

    def delete_preset(self, name: str) -> bool:
        """Delete a preset. 'mira' is protected."""
        if name not in self.presets or name == "mira":
            return False
        del self.presets[name]
        if self.active_preset == name:
            self._load_preset("mira")
        self._save_config()
        return True

    def _save_config(self):
        """Persist presets back to config.json."""
        try:
            self.config["personality_presets"] = self.presets
            Path(self.config_path).write_text(json.dumps(self.config, indent=2))
        except Exception:
            pass

    # ── Mood selection ───────────────────────────────────────────────────

    def pick_mood(self, user_input: str, preferred_mood: str = None) -> Dict:
        """Pick a mood from keyword signals, sentiment, patience pressure,
        and emotional momentum. Confidence reflects how dominant the winner is."""
        if not self._can_change_mood():
            return {"mood": self.mood, "confidence": self.mood_confidence, "top_k": self.top_k}

        scores = self._keyword_scores(user_input)
        self._apply_sentiment_boost(user_input, scores)
        self._apply_context_biases(user_input, scores)
        self._apply_emotional_momentum(scores)
        if preferred_mood and preferred_mood in self.MOODS:
            scores[preferred_mood] = scores.get(preferred_mood, 0) + 1.5

        total = sum(scores.values()) + 1e-6
        sorted_moods = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top3 = sorted_moods[:3]
        top_mood, top_score = top3[0]
        second_score = top3[1][1] if len(top3) > 1 else 0.0

        # Confidence: dominance across all moods + gap over the runner-up.
        dominance = top_score / total
        gap = top_score / (top_score + second_score + 1e-6)
        confidence = dominance * 0.6 + gap * 0.4

        # Emotional inertia: negative moods resist neutral inputs, and the
        # current mood gets heavy weight so she doesn't flap back and forth.
        if self.mood in self.STICKY_NEGATIVE and confidence < self.mood_confidence_threshold + 0.25:
            final_mood = self.mood
        elif self.mood in [m for m, _ in top3] and confidence < self.mood_confidence_threshold + 0.15:
            final_mood = self.mood
        elif confidence < self.mood_confidence_threshold:
            final_mood = self.mood
        elif top_mood == self.mood:
            final_mood = self.mood
        else:
            final_mood = top_mood

        return {"mood": final_mood, "confidence": round(confidence, 2), "top_k": top3}

    def _keyword_scores(self, user_input: str) -> Dict[str, float]:
        lower = user_input.lower()
        scores = {m: 0.0 for m in self.MOODS}
        for mood, keywords in self.MOOD_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    scores[mood] += max(1.0, len(kw) / 4.0)
        return scores

    def _apply_sentiment_boost(self, user_input: str, scores: Dict[str, float]):
        lower = user_input.lower()
        for mood, words in self.SENTIMENT_BOOST.items():
            for w in words:
                if w in lower:
                    scores[mood] += 1.5

    def _apply_context_biases(self, user_input: str, scores: Dict[str, float]):
        if self.patience < 0.15:
            scores["sad"] += 2.0
        angry_thr = self.patience_config.get("angry_threshold", 0.45)
        if self.patience <= angry_thr:
            scores["angry"] += 2.0
        if self._detect_hurtful(user_input):
            scores["sad"] += 3.0

    def _apply_emotional_momentum(self, scores: Dict[str, float]):
        """Recent insults/shutdowns keep negative moods elevated; low patience
        makes anger sticky; the current mood gets a small inertia boost."""
        try:
            events = self.memory.recent_events(20)
            insult_count = sum(1 for e in events if e.get("event_type") == "insult")
            shutdown_count = sum(1 for e in events if e.get("event_type") == "shutdown")

            if insult_count:
                scores["angry"] += min(insult_count, 2) * 0.15
                scores["sad"] += min(insult_count, 2) * 0.10
            if shutdown_count:
                scores["sad"] += min(shutdown_count, 2) * 0.15
                scores["angry"] += min(shutdown_count, 2) * 0.15
            if self.patience < 0.3:
                scores["angry"] += 0.3 - self.patience
            # Strong inertia: the current mood gets heavy weight so she
            # doesn't flap between moods on every message.
            if self.mood in scores:
                scores[self.mood] += 1.2
        except Exception:
            pass

    def _detect_hurtful(self, user_input: str = "") -> bool:
        lower = (user_input or "").lower()
        directed_at_mira = any(p in lower for p in ["you ", "u ", "mira", "your ", "ur "])
        hurtful = [
            "i hate you", "i hate u", "i dont need you", "i dont need u",
            "youre useless", "ur useless", "you're useless",
            "go away", "leave me alone", "nobody cares",
        ]
        return directed_at_mira and any(p in lower for p in hurtful)

    def maybe_drift(self):
        """Small chance to drift to a random mood when idle (boredom/curiosity)."""
        if not self._can_change_mood():
            return
        chance = self.mood_drift.get("chance", 0.0)
        if chance <= 0 or random.random() > chance:
            return
        weights = self.mood_drift.get("weights", {})
        moods = [m for m in weights if m in self.MOODS]
        if not moods:
            return
        new_mood = random.choices(moods, weights=[weights[m] for m in moods], k=1)[0]
        if new_mood != self.mood:
            self.set_mood(new_mood, force=True, trigger="drift")

    def _can_change_mood(self, force: bool = False) -> bool:
        if force:
            return True
        return time.time() - self.mood_changed_at >= self.mood_cooldown

    def set_mood(self, mood: str, force: bool = False, confidence: float = None,
                 top_k: List[Tuple[str, float]] = None, trigger: str = ""):
        if mood in self.MOODS and self._can_change_mood(force):
            old_mood = self.mood
            self.mood = mood
            self.mood_changed_at = time.time()
            if confidence is not None:
                self.mood_confidence = confidence
            if top_k:
                self.top_k = top_k
            try:
                if old_mood != self.mood or trigger:
                    self.memory.log_mood(self.mood, trigger or "", self.mood_confidence)
            except Exception:
                pass

    # ── Patience ─────────────────────────────────────────────────────────

    def adjust(self, delta: float):
        """Adjust patience by delta, clamped to [0, 1]."""
        self.patience = min(1.0, max(0.0, self.patience + delta))
        self.last_interaction = datetime.now().isoformat()

    def annoy(self, amount: float = 0.25, reason: str = ""):
        """Drain patience. Lower patience = she gets annoyed faster and harder."""
        multiplier = 1.0 + (1.0 - self.patience) * 0.8
        self.patience = max(0.0, self.patience - amount * multiplier)
        if reason:
            self.annoyance_reason = reason

    def comfort(self, amount: float = 0.05):
        """Recover patience (friendly chat, apologies)."""
        self.adjust(amount)

    # ── Update loop ──────────────────────────────────────────────────────

    def update(self, user_input: str = None, preferred_mood: str = None):
        self._decay_patience()
        if user_input:
            mood_data = self.pick_mood(user_input, preferred_mood=preferred_mood)
            self.set_mood(
                mood_data["mood"],
                confidence=mood_data["confidence"],
                top_k=mood_data["top_k"],
                trigger=user_input,
            )
        self._update_mood_from_patience()
        self._save_state()

    def _decay_patience(self):
        """Patience slowly decays with real time away from the terminal."""
        if self.last_interaction:
            try:
                last = datetime.fromisoformat(self.last_interaction)
                hours = (datetime.now() - last).total_seconds() / 3600
                decay = self.patience_config.get("decay_per_hour", 0.05) * hours
                self.patience = max(0.0, self.patience - decay)
            except Exception:
                pass

    def _update_mood_from_patience(self):
        """Patience is the emotional pressure gauge: at low patience she is
        genuinely angry/sad regardless of what the keyword classifier guessed.
        Forced (bypasses cooldown) so the header reflects reality."""
        if self.patience < 0.15:
            self.set_mood("sad", force=True, trigger="patience")
        elif self.patience < 0.35:
            self.set_mood("angry", force=True, trigger="patience")
        elif self.mood in ("angry", "sad") and self.patience >= 0.5:
            # Recovered enough — she settles back down.
            self.set_mood("calm", force=True, trigger="patience recovered")

    # ── State ────────────────────────────────────────────────────────────

    def state(self) -> Dict:
        return {
            "name": self.name,
            "mood": self.mood,
            "mood_confidence": round(self.mood_confidence, 2),
            "top_k": self.top_k,
            "patience": round(self.patience, 2),
        }

    def _save_state(self):
        self.memory.save_state({
            "mood": self.mood,
            "last_interaction": self.last_interaction,
            "patience": self.patience,
            "annoyance_reason": self.annoyance_reason,
            "relationship": self.relationship,
        })
