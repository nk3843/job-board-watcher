from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


@dataclass
class Company:
    name: str
    ats: str            # greenhouse | lever | ashby | smartrecruiters | workday
    board: str          # board token / slug / id, or the full Workday careers URL
    list: str = "Main"
    priority: str = ""

    @property
    def key(self) -> str:
        return f"{self.name}|{self.ats}|{self.board}"


@dataclass
class Job:
    company: str
    ats: str
    job_id: str
    title: str
    location: str
    url: str
    posted_at: Optional[datetime] = None   # UTC; None when the ATS gives no posting date
    country: Optional[str] = None          # country code or name when the ATS provides one
    tracks: Tuple[str, ...] = ()
    list: str = "Main"
    priority: str = ""
    resume: str = ""                       # which resume version to send
    highlight: bool = False                # location matches highlight_locations (default: remote)

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.company}:{self.job_id}"
