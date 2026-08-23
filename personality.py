import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from memory import Memory


class Personality:
    MOODS = ["calm", "happy", "scared", "angry", "confused", "sad"]

    EMOTION_CATEGORIES = {
        "Positive": ["calm", "happy"],
        "Negative": ["scared", "angry", "sad"],
        "Cognitive": ["confused"],
    }

    MOOD_KEYWORDS: Dict[str, List[str]] = {
        "calm": ["calm", "chill", "relax", "okay", "fine", "alright"],
        "happy": ["happy", "glad", "yay", "nice", "good", "great", "awesome", "lol", ":D", ":3", "^_^"],
        "scared": ["scared", "afraid", "fear", "terrified", "spooky", "horror", "creepy"],
        "angry": ["angry", "mad", "pissed", "shut up", "ugh", "hate", "idiot", "stupid"],
        "confused": ["confused", "dont understand", "lost", "what do you mean", "huh", "idk"],
        "sad": ["sad", "cry", "depressed", "upset", "lonely", "hurt", "miss", "sorry", ":("],
    }

    SENTIMENT_BOOST: Dict[str, List[str]] = {
        "happy": ["good", "great", "awesome", "love", "nice"],
        "sad": ["bad", "terrible", "awful", "cry", "upset"],
        "angry": ["hate", "mad", "pissed", "furious", "rage"],
        "scared": ["scared", "afraid", "terrified"],
        "confused": ["confused", "dont understand"],
    }

    def __init__(self, config_path: str = "config.json"):
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

        # mood classifier config
        self.mood_classifier = self.config.get("mood_classifier", "keyword")
        self.mood_confidence_threshold = self.config.get("mood_confidence_threshold", 0.35)

        state = self.memory.get_state()
        # Patience is Mira's single tolerance stat. Higher = more tolerant and lively.
        saved_mood = state.get("mood", "calm")
        saved_patience = state.get("patience", 1.0)
        self.annoyance_reason = state.get("annoyance_reason", "")

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
                recent_events = self.memory.recent_events(10)
                if any(e.get("event_type") == "shutdown" for e in recent_events):
                    self.mood = "angry"
                    self.patience = min(self.patience, 0.35)
            except Exception:
                pass
        self.mood_changed_at = 0.0
        self.mood_cooldown = 8.0
        self.last_interaction = state.get("last_interaction")
        self.annoyance_reason = ""

        # V26.3 mood confidence / top-k
        self.mood_confidence = 1.0
        self.top_k: List[Tuple[str, float]] = []

        # Personality preset
        self.name = "Mira"
        self.voice = ""
        self._load_preset(self.active_preset)

    def _load_preset(self, preset_name: str):
        """Load a personality preset from config."""
        preset = self.presets.get(preset_name, self.presets.get("mira", {}))
        self.name = preset.get("name", self.config.get("name", "Mira"))
        self.voice = preset.get("voice", self.config.get("voice", ""))
        self.active_preset = preset_name

    def switch_preset(self, preset_name: str) -> bool:
        """Switch to another personality preset."""
        if preset_name not in self.presets:
            return False
        self._load_preset(preset_name)
        return True

    # ── Mood classification ──────────────────────────────────────────────

    def pick_mood(self, user_input: str, llm=None, preferred_mood: str = None) -> Dict:
        """Pick a mood from the 27 emotions with confidence and top-k alternatives.

        Confidence is based on how dominant the top mood is across all 27 scores,
        and how far ahead it is from the runner-up. Recent emotional momentum
        (insults, praise, etc.) biases the scores so one calm sentence cannot
        instantly erase anger.
        """
        if not self._can_change_mood():
            return {"mood": self.mood, "confidence": self.mood_confidence, "top_k": self.top_k}

        scores = self._keyword_scores(user_input)
        self._apply_sentiment_boost(user_input, scores)
        self._apply_context_biases(user_input, scores)
        self._apply_mood_memory_bias(scores)
        self._apply_emotional_momentum(scores)
        if preferred_mood and preferred_mood in self.MOODS:
            scores[preferred_mood] = scores.get(preferred_mood, 0) + 1.5

        total = sum(scores.values()) + 1e-6
        sorted_moods = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top3 = [(m, s) for m, s in sorted_moods[:3]]
        top_mood, top_score = top3[0]
        second_score = top3[1][1] if len(top3) > 1 else 0.0

        # Normalized confidence: dominance across all moods
        dominance = top_score / total
        # Gap confidence: how far ahead the winner is vs runner-up
        gap = top_score / (top_score + second_score + 1e-6)
        # Combined confidence: a clear winner far ahead of both the field and 2nd place
        confidence = (dominance * 0.6 + gap * 0.4)

        if llm is not None and self.mood_classifier in ("llm", "hybrid"):
            if self.mood_classifier == "llm" or confidence < self.mood_confidence_threshold:
                llm_mood, llm_conf = self._llm_classify_mood(user_input, llm)
                if llm_mood in scores and llm_conf > confidence:
                    top_mood = llm_mood
                    confidence = llm_conf

        # Emotional inertia: negative/sticky moods resist neutral inputs.
        sticky_negative = {"angry", "sad", "scared"}
        if self.mood in sticky_negative:
            # Need stronger evidence to leave a negative mood
            if confidence < self.mood_confidence_threshold + 0.18:
                final_mood = self.mood
            else:
                final_mood = top_mood
        elif self.mood in [m for m, _ in top3] and confidence < self.mood_confidence_threshold + 0.10:
            final_mood = self.mood
        elif confidence < self.mood_confidence_threshold:
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

    def _apply_mood_memory_bias(self, scores: Dict[str, float]):
        """Slightly penalize moods that have appeared very recently to reduce repetition."""
        try:
            recent = self.memory.recent_moods(5)
            for entry in recent:
                m = entry.get("mood")
                if m in scores:
                    scores[m] = max(0.0, scores[m] - 0.3)
        except Exception:
            pass

    def _apply_emotional_momentum(self, scores: Dict[str, float]):
        """Boost or dampen moods based on recent significant events and current state.

        If she was recently insulted or got fed up, negative moods stay elevated.
        If she was recently comforted or praised, positive moods rise faster.
        """
        try:
            recent_events = self.memory.recent_events(20)
            insult_count = sum(1 for e in recent_events if e.get("event_type") == "insult")
            shutdown_count = sum(1 for e in recent_events if e.get("event_type") == "shutdown")

            # Insults create emotional momentum toward anger/sad, but fade quickly
            if insult_count:
                scores["angry"] = scores.get("angry", 0) + min(insult_count, 2) * 0.15
                scores["sad"] = scores.get("sad", 0) + min(insult_count, 2) * 0.10

            # Shutdowns create lingering resentment / sadness
            if shutdown_count:
                scores["sad"] = scores.get("sad", 0) + min(shutdown_count, 2) * 0.15
                scores["angry"] = scores.get("angry", 0) + min(shutdown_count, 2) * 0.15

            # Low patience keeps negative moods sticky
            if self.patience < 0.3:
                scores["angry"] = scores.get("angry", 0) + (0.3 - self.patience)

            # Current mood also gets a small inertia boost
            if self.mood in scores:
                scores[self.mood] = scores.get(self.mood, 0) + 0.5
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

    def _llm_classify_mood(self, user_input: str, llm) -> Tuple[str, float]:
        """Ask the LLM for the best mood. Returns (mood, confidence)."""
        try:
            prompt = (
                "Pick the single best mood for an AI companion named Mira based on the user's message.\n"
                f"Valid moods: {', '.join(self.MOODS)}\n"
                "Return ONLY JSON in this exact format:\n"
                '{"mood":"happy","confidence":0.92,"top_k":[{"mood":"happy","score":0.92},...]}\n\n'
                f"User message: {user_input}\n"
            )
            text = llm.generate_text(prompt=prompt)
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if not match:
                return (self.mood, 0.0)
            data = json.loads(match.group(0))
            mood = data.get("mood", self.mood)
            confidence = float(data.get("confidence", 0.0))
            if mood in self.MOODS:
                return (mood, confidence)
        except Exception:
            pass
        return (self.mood, 0.0)

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

    # ── Update loop ──────────────────────────────────────────────────────

    def update(self, user_input: str = None, llm=None, preferred_mood: str = None):
        self._decay_patience()
        self._recover_patience()

        if user_input:
            # Keyword classifier drives the mood, but an LLM-picked emotion gets a boost.
            mood_data = self.pick_mood(user_input, llm=llm, preferred_mood=preferred_mood)
            self.set_mood(
                mood_data["mood"],
                confidence=mood_data["confidence"],
                top_k=mood_data["top_k"],
                trigger=user_input,
            )

        self._update_mood_from_patience()
        self._save_state()

    def _decay_patience(self):
        if self.last_interaction:
            last = datetime.fromisoformat(self.last_interaction)
            hours = (datetime.now() - last).total_seconds() / 3600
            decay = self.patience_config.get("decay_per_hour", 0.05) * hours
            self.patience = max(0.0, self.patience - decay)

    def _recover_patience(self):
        recovery = self.patience_config.get("recover_per_interaction", 0.01) * 0.5
        if self.annoyance_reason and random.random() < 0.3:
            self.annoyance_reason = ""
        self.patience = min(1.0, self.patience + recovery)

    def _update_mood_from_patience(self):
        if self.mood_confidence >= 0.8:
            return

        if self.patience < 0.15:
            self.mood = "sad"
        elif self.patience < 0.35:
            self.mood = "angry"

    # ── Interaction helpers ──────────────────────────────────────────────

    def interact(self, intensity: float = 0.05, recover_patience: bool = True):
        self.last_interaction = datetime.now().isoformat()
        if recover_patience:
            self.patience = min(1.0, self.patience + self.patience_config.get("recover_per_interaction", 0.01) * 0.5 + intensity * 0.3)

    def drain_patience(self, amount: float = 0.03):
        if self.mood in ("angry", "sad"):
            amount *= 1.5
        self.patience = max(0.0, self.patience - amount)

    def recover_patience_idle(self, amount: float = 0.01):
        """Recover a small amount of patience when idle."""
        self.patience = min(1.0, self.patience + amount)

    def annoy(self, amount: float = 0.25, reason: str = ""):
        # Lower patience = she gets annoyed faster and harder
        multiplier = 1.0 + (1.0 - self.patience) * 0.8
        self.patience = max(-1.0, self.patience - amount * multiplier)
        if reason:
            self.annoyance_reason = reason

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
        })
