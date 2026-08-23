import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


class Memory:
    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(exist_ok=True)

        self.state_path = self.memory_dir / "state.json"
        self.log_path = self.memory_dir / "interactions.jsonl"
        self.facts_path = self.memory_dir / "facts.jsonl"
        self.events_path = self.memory_dir / "events.jsonl"
        self.short_term_path = self.memory_dir / "short_term.json"
        self.moods_path = self.memory_dir / "moods.jsonl"

    # ── Persistent state ───────────────────────────────────────────────

    def save_state(self, state: Dict[str, Any]):
        self.state_path.write_text(json.dumps(state, indent=2))

    def get_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {}

    # ── Conversation log ───────────────────────────────────────────────

    def clear_interactions(self):
        """Clear the current session's conversation log. Facts and state are preserved."""
        if self.log_path.exists():
            self.log_path.write_text("")

    def log_interaction(self, role: str, message: str, session_id: str = None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "message": message,
        }
        if session_id:
            entry["session_id"] = session_id
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def summarize_session(self, session_id, llm):
        """Return a concise summary of the current session."""
        interactions = self.recent_interactions(20, session_id)
        facts = self.all_facts()[-10:]
        events = self.recent_events(10)
        if not interactions and not facts and not events:
            return "nothing to summarize yet."
        parts = [
            "Summarize this session briefly from Mira's perspective.",
            "",
            f"Recent interactions ({len(interactions)}):",
        ]
        for i in interactions:
            parts.append(f"- {i['role']}: {i['message'][:80]}")
        parts.append("")
        parts.append(f"Facts ({len(facts)}):")
        for f in facts:
            parts.append(f"- {f}")
        parts.append("")
        parts.append(f"Events ({len(events)}):")
        for e in events:
            parts.append(f"- {e.get('event_type')}: {e.get('detail')}")
        prompt = "\n".join(parts)
        if llm is not None:
            try:
                return llm.generate_text(prompt=prompt)
            except Exception as exc:
                return f"error summarizing session: {exc}"
        return prompt

    def recent_interactions(self, n: int = 20, session_id: str = None) -> List[Dict]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text().strip().split("\n")
        records = [json.loads(line) for line in lines if line]
        if session_id:
            records = [r for r in records if r.get("session_id") == session_id]
        return records[-n:]

    # ── Long-term facts ────────────────────────────────────────────────

    def remember_fact(self, subject: str, fact: str):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "subject": subject.lower(),
            "fact": fact,
        }
        with self.facts_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def facts_about(self, subject: str) -> List[str]:
        if not self.facts_path.exists():
            return []
        subject_lower = subject.lower()
        facts = []
        for line in self.facts_path.read_text().strip().split("\n"):
            if not line:
                continue
            entry = json.loads(line)
            if entry["subject"] == subject_lower:
                facts.append(entry["fact"])
        return facts

    def all_facts(self) -> List[str]:
        if not self.facts_path.exists():
            return []
        facts = []
        for line in self.facts_path.read_text().strip().split("\n"):
            if not line:
                continue
            entry = json.loads(line)
            fact = entry.get("fact", "")
            # Skip code dumps, spam, and very long facts
            if len(fact) > 120 or "def " in fact or "class " in fact:
                continue
            facts.append(fact)
        return facts

    # ── Event memory (significant things that affect Mira's attitude) ───

    def log_event(self, event_type: str, detail: str = "", severity: float = 1.0):
        """Log a notable event, e.g. ('insult', 'user called mira stupid', 2.0)."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "detail": detail,
            "severity": severity,
        }
        with self.events_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def recent_events(self, n: int = 20) -> List[Dict]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text().strip().split("\n")
        records = [json.loads(line) for line in lines if line]
        return records[-n:]

    def memory_summary(self, n: int = 10) -> str:
        events = self.recent_events(n)
        if not events:
            return ""
        lines = []
        for ev in events:
            lines.append(f"- {ev.get('event_type')}: {ev.get('detail')}")
        return "\n".join(lines)

    def events_of_type(self, event_type: str) -> List[Dict]:
        if not self.events_path.exists():
            return []
        records = self.recent_events(1000)
        return [r for r in records if r.get("event_type") == event_type]

    def get_grudge_summary(self) -> str:
        """Return a short summary of recent negative/positive events for the prompt."""
        events = self.recent_events(50)
        insults = [e for e in events if e.get("event_type") == "insult"]
        shutdowns = [e for e in events if e.get("event_type") == "shutdown"]
        if not events:
            return ""
        parts = []
        if shutdowns:
            parts.append(f"session previously ended because Mira got fed up")
        if insults:
            parts.append(f"user has insulted Mira {len(insults)} times recently")
        return "; ".join(parts)

    # ── Short-term working memory (current session context) ─────────────

    def save_short_term(self, data: Dict[str, Any]):
        self.short_term_path.write_text(json.dumps(data, indent=2))

    def get_short_term(self) -> Dict[str, Any]:
        if self.short_term_path.exists():
            return json.loads(self.short_term_path.read_text())
        return {}

    # ── Mood memory ──────────────────────────────────────────────────────

    def log_mood(self, mood: str, trigger: str = "", confidence: float = 1.0):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "mood": mood,
            "trigger": trigger,
            "confidence": confidence,
        }
        with self.moods_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def recent_moods(self, n: int = 20) -> List[Dict]:
        if not self.moods_path.exists():
            return []
        lines = self.moods_path.read_text().strip().split("\n")
        records = [json.loads(line) for line in lines if line]
        return records[-n:]
