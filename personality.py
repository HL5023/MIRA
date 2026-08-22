import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict

from memory import Memory


class Personality:
    # Reduced mood set requested by user
    MOODS = ["normal", "happy", "curious", "mischievous", "annoyed", "angry", "sad"]

    def __init__(self, config_path: str = "config.json"):
        self.config = json.loads(Path(config_path).read_text())
        self.memory = Memory()

        self.name = self.config.get("name", "Mira")
        self.voice = self.config.get("voice", "")
        self.traits = self.config.get("personality", {})
        self.energy_config = self.config.get("energy", {})
        self.mood_drift = self.config.get("mood_drift", {})
        self.patience_config = self.config.get("patience", {})

        state = self.memory.get_state()
        # Start fresh each session so she doesn't boot up exhausted/sad.
        self.energy = self.energy_config.get("base", 0.7)
        self.mood = "normal"
        self.mood_changed_at = 0.0
        self.mood_cooldown = 15.0  # seconds between non-forced mood changes
        self.last_interaction = state.get("last_interaction")
        self.patience = self.patience_config.get("base", 0.7)
        self.annoyance_reason = ""

    def _pick_mood(self) -> str:
        weights = self.mood_drift.get("weights", {})
        # Only drift into moods we actually use
        choices = [m for m in self.MOODS if m in weights]
        if not choices:
            choices = [m for m in self.MOODS if m not in ("annoyed", "angry", "sad")]
        probs = [max(weights.get(m, 0.1), 0.05) for m in choices]
        return random.choices(choices, weights=probs, k=1)[0]

    def _update_mood_from_sentiment(self, user_input: str):
        # Only allow sadness from user sadness or hurtful words.
        # No more keyword-based curious/annoyed/angry/happy flipping.
        if not self._can_change_mood():
            return

        lower = user_input.lower()

        # User shares sadness/bad news -> Mira feels sad
        user_sad = [
            "im sad", "i'm sad", "im depressed", "i'm depressed", "i cried", "im crying",
            "im upset", "im lonely", "im hurt", "bad day", "my day was bad",
            "i got suspended", "im suspended", "i'm suspended", "im in trouble",
            ":(", "sad rn", "feeling down", "i failed",
        ]
        if any(p in lower for p in user_sad):
            self.set_mood("sad")
            return

        # Hurtful words directed at Mira -> Mira feels sad
        directed_at_mira = any(p in lower for p in ["you ", "u ", "mira", "your ", "ur "])
        hurtful = [
            "i hate you", "i hate u", "i dont need you", "i dont need u",
            "youre useless", "ur useless", "you're useless",
            "go away", "leave me alone", "nobody cares",
        ]
        if directed_at_mira and any(p in lower for p in hurtful):
            self.set_mood("sad")

    def _can_change_mood(self, force: bool = False) -> bool:
        if force:
            return True
        return time.time() - self.mood_changed_at >= self.mood_cooldown

    def set_mood(self, mood: str, force: bool = False):
        if mood in self.MOODS and self._can_change_mood(force):
            self.mood = mood
            self.mood_changed_at = time.time()

    def update(self, user_input: str = None):
        self._decay_energy()
        self._maybe_drift_mood()
        self._recover_patience()
        self._update_mood_from_energy()
        self._update_mood_from_patience()
        if user_input:
            self._update_mood_from_sentiment(user_input)
        self._save_state()

    def _update_mood_from_energy(self):
        # Only get sad from very low energy. Otherwise let sentiment/patience drive mood.
        if self.energy < 0.05:
            self.mood = "sad"

    def _decay_energy(self):
        if self.last_interaction:
            last = datetime.fromisoformat(self.last_interaction)
            hours = (datetime.now() - last).total_seconds() / 3600
            decay = self.energy_config.get("decay_per_hour", 0.05) * hours
            self.energy = max(0.0, self.energy - decay)

    def _maybe_drift_mood(self):
        if random.random() < self.mood_drift.get("chance", 0.3):
            # don't drift into negative moods naturally, only via patience/sentiment
            base_moods = [m for m in self.MOODS if m not in ("annoyed", "angry", "sad")]
            weights = self.mood_drift.get("weights", {})
            probs = [max(weights.get(m, 0.1), 0.05) for m in base_moods]
            self.mood = random.choices(base_moods, weights=probs, k=1)[0]

    def _recover_patience(self):
        # Recover faster, and let old annoyances fade so patience can climb again.
        recovery = self.patience_config.get("recover_per_interaction", 0.01) * 2
        if self.annoyance_reason and random.random() < 0.3:
            self.annoyance_reason = ""
        self.patience = min(1.0, self.patience + recovery)

    def _update_mood_from_patience(self):
        # Negative moods from patience override base mood, but also recover.
        annoyed_thr = self.patience_config.get("annoyed_threshold", 0.35)
        angry_thr = self.patience_config.get("angry_threshold", 0.0)
        furious_thr = self.patience_config.get("furious_threshold", -0.4)

        if self.patience <= furious_thr:
            self.mood = "angry"
        elif self.patience <= angry_thr:
            self.mood = "angry"
        elif self.patience <= annoyed_thr:
            self.mood = "annoyed"
        elif self.mood in ("annoyed", "angry"):
            self.mood = "normal"

    def interact(self, intensity: float = 0.05, recover_patience: bool = True):
        self.last_interaction = datetime.now().isoformat()
        self.energy = min(self.energy_config.get("max", 1.0), self.energy + intensity)
        if recover_patience:
            self.patience = min(1.0, self.patience + self.patience_config.get("recover_per_interaction", 0.01))

    def drain_energy(self, amount: float = 0.03):
        # Angry and sad drain energy faster
        if self.mood in ("angry", "sad"):
            amount *= 1.5
        self.energy = max(0.0, self.energy - amount)

    def recover_energy_idle(self, amount: float = 0.01):
        """Recover a small amount of energy when idle."""
        self.energy = min(self.energy_config.get("max", 1.0), self.energy + amount)

    def annoy(self, amount: float = 0.25, reason: str = ""):
        # Lower energy = patience drains faster. At 0 energy, annoyance hurts 3x as much.
        multiplier = 1.0 + (1.0 - self.energy) * 2.0
        self.patience = max(-1.0, self.patience - amount * multiplier)
        if reason:
            self.annoyance_reason = reason

    def state(self) -> Dict:
        return {
            "name": self.name,
            "mood": self.mood,
            "energy": round(self.energy, 2),
            "patience": round(self.patience, 2),
        }

    def _save_state(self):
        self.memory.save_state({
            "energy": self.energy,
            "mood": self.mood,
            "last_interaction": self.last_interaction,
            "patience": self.patience,
            "annoyance_reason": self.annoyance_reason,
        })
