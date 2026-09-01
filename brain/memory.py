import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class Memory:
    """JSONL-backed persistence for facts, events, moods, and conversation."""

    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(exist_ok=True)

        self.state_path = self.memory_dir / "state.json"
        self.log_path = self.memory_dir / "interactions.jsonl"
        self.facts_path = self.memory_dir / "facts.jsonl"
        self.events_path = self.memory_dir / "events.jsonl"
        self.moods_path = self.memory_dir / "moods.jsonl"

    # ── Generic JSONL helpers ────────────────────────────────────────────

    def _append(self, path: Path, entry: Dict[str, Any]):
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def _read(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    # ── Persistent state ───────────────────────────────────────────────

    def save_state(self, state: Dict[str, Any]):
        self.state_path.write_text(json.dumps(state, indent=2))

    def get_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {}

    # ── Conversation log ───────────────────────────────────────────────

    def log_interaction(self, role: str, message: str, session_id: str = None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "message": message,
        }
        if session_id:
            entry["session_id"] = session_id
        self._append(self.log_path, entry)

    def recent_interactions(self, n: int = 20, session_id: str = None) -> List[Dict]:
        records = self._read(self.log_path)
        if session_id:
            records = [r for r in records if r.get("session_id") == session_id]
        return records[-n:]

    # ── Long-term facts ────────────────────────────────────────────────

    def remember_fact(self, subject: str, fact: str):
        # Skip near-duplicate facts so memory stays clean
        fact_lower = fact.lower()
        for existing in self.facts_about(subject):
            existing_lower = existing.lower()
            if existing_lower == fact_lower or existing_lower in fact_lower or fact_lower in existing_lower:
                return
        self._append(self.facts_path, {
            "timestamp": datetime.now().isoformat(),
            "subject": subject.lower(),
            "fact": fact,
        })

    def forget_fact(self, fact: str) -> str:
        """Remove the first fact containing the given text. Returns the removed fact or ''."""
        if not self.facts_path.exists():
            return ""
        kept = []
        removed = ""
        for line in self.facts_path.read_text().splitlines():
            if not line:
                continue
            entry = json.loads(line)
            if not removed and fact.lower() in entry.get("fact", "").lower():
                removed = entry["fact"]
                continue
            kept.append(line)
        self.facts_path.write_text("\n".join(kept) + ("\n" if kept else ""))
        return removed

    def facts_about(self, subject: str) -> List[str]:
        subject_lower = subject.lower()
        return [e["fact"] for e in self._read(self.facts_path) if e.get("subject") == subject_lower]

    def all_facts(self) -> List[str]:
        facts = []
        for e in self._read(self.facts_path):
            fact = e.get("fact", "")
            # Skip code dumps, spam, and very long facts
            if len(fact) > 120 or "def " in fact or "class " in fact:
                continue
            facts.append(fact)
        return facts

    # ── Event memory (things that affect Mira's attitude) ───────────────

    def log_event(self, event_type: str, detail: str = "", severity: float = 1.0):
        self._append(self.events_path, {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "detail": detail,
            "severity": severity,
        })

    def recent_events(self, n: int = 20) -> List[Dict]:
        return self._read(self.events_path)[-n:]

    def memory_summary(self, n: int = 10) -> str:
        events = self.recent_events(n)
        if not events:
            return ""
        return "\n".join(f"- {e.get('event_type')}: {e.get('detail')}" for e in events)

    def get_grudge_summary(self) -> str:
        """Short summary of recent negative events for the prompt."""
        events = self.recent_events(50)
        insults = [e for e in events if e.get("event_type") == "insult"]
        shutdowns = [e for e in events if e.get("event_type") == "shutdown"]
        if not events:
            return ""
        parts = []
        if shutdowns:
            parts.append("session previously ended because Mira got fed up")
        if insults:
            parts.append(f"user has insulted Mira {len(insults)} times recently")
        return "; ".join(parts)

    # ── Mood memory ──────────────────────────────────────────────────────

    def log_mood(self, mood: str, trigger: str = "", confidence: float = 1.0):
        self._append(self.moods_path, {
            "timestamp": datetime.now().isoformat(),
            "mood": mood,
            "trigger": trigger,
            "confidence": confidence,
        })

    def recent_moods(self, n: int = 20) -> List[Dict]:
        return self._read(self.moods_path)[-n:]
