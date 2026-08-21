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
        self.short_term_path = self.memory_dir / "short_term.json"

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
        return [json.loads(line)["fact"] for line in self.facts_path.read_text().strip().split("\n") if line]

    # ── Short-term working memory (current session context) ─────────────

    def save_short_term(self, data: Dict[str, Any]):
        self.short_term_path.write_text(json.dumps(data, indent=2))

    def get_short_term(self) -> Dict[str, Any]:
        if self.short_term_path.exists():
            return json.loads(self.short_term_path.read_text())
        return {}
