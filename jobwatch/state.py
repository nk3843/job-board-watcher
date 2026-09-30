"""Remembers which jobs have been seen, so each posting is reported once."""
import json
import os
from datetime import date, timedelta


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = {"version": 1, "companies": {}, "jobs": {}}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.data["companies"] = loaded.get("companies", {})
            self.data["jobs"] = loaded.get("jobs", {})

    def company_known(self, key: str) -> bool:
        return key in self.data["companies"]

    def mark_company(self, key: str, today: str) -> None:
        self.data["companies"].setdefault(key, today)

    def seen(self, job_key: str) -> bool:
        return job_key in self.data["jobs"]

    def touch(self, job_key: str, today: str) -> None:
        entry = self.data["jobs"].get(job_key)
        self.data["jobs"][job_key] = {"first": entry["first"] if entry else today, "last": today}

    def prune(self, today: str, keep_days: int) -> int:
        cutoff = (date.fromisoformat(today) - timedelta(days=keep_days)).isoformat()
        stale = [k for k, v in self.data["jobs"].items() if v.get("last", "") < cutoff]
        for k in stale:
            del self.data["jobs"][k]
        return len(stale)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=0, sort_keys=True)
        os.replace(tmp, self.path)
