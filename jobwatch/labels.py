"""Per-posting labels for the digest: which resume to send, and whether the location is highlighted.

Both are set in config.yaml (see config.example.yaml):

  resume_rules:            # optional; the rule whose keyword appears earliest in the title wins
    - resume: AI Engineer
      keywords: [ai, llm, agents]
  default_resume: Backend  # label for titles no rule matches (blank = no label)
  highlight_locations: [seattle, bellevue, remote]   # listed first in the digest; default: remote postings
"""
from .filters import compile_keywords

DEFAULT_HIGHLIGHT = ["remote", "virtual", "anywhere", "work from home"]


class Labeler:
    def __init__(self, cfg: dict):
        rules = cfg.get("resume_rules") or []
        self.rules = [(r["resume"], compile_keywords(r.get("keywords") or [])) for r in rules if r.get("resume")]
        self.default = cfg.get("default_resume") or ""
        self.highlight_re = compile_keywords(cfg.get("highlight_locations") or DEFAULT_HIGHLIGHT)

    def resume(self, title: str) -> str:
        """The rule whose keyword appears earliest in the title wins ("Machine Learning Engineer, On-device AI"
        is an ML role; "AI Engineer, applied ML" is an AI role). Ties go to the rule listed first."""
        t = (title or "").lower()
        best = None
        for order, (name, rx) in enumerate(self.rules):
            m = rx.search(t) if rx else None
            if m and (best is None or (m.start(), order) < best[0]):
                best = ((m.start(), order), name)
        return best[1] if best else self.default

    def highlight(self, location: str) -> bool:
        return bool(self.highlight_re and self.highlight_re.search((location or "").lower()))
