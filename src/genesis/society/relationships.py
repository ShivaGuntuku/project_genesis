"""Directional relationships. See docs/04-society.md (4.2).

Mira → Kael can differ from Kael → Mira. Feelings change only through recorded events,
using the fixed EFFECTS table below. Labels (friend, rival, ...) are derived, never assigned.
"""

from dataclasses import asdict, dataclass

SIGNED = {"affinity"}  # -1..1; all others 0..1
STRANGER_TRUST = 0.3  # how much you trust someone you know nothing bad or good about

# What each kind of experience does to the feelings of the one who experienced it.
EFFECTS: dict[str, dict[str, float]] = {
    "encounter": {"familiarity": 0.05},  # a new meeting (after being apart)
    "greeted": {"familiarity": 0.03, "affinity": 0.02},
    "chatted": {"familiarity": 0.03, "affinity": 0.04},  # first small talk of the day
    "told_info": {"trust": 0.05, "affinity": 0.05, "gratitude": 0.05},
    "lied_to": {"trust": -0.35, "affinity": -0.2, "grievance": 0.3},
    "gift_in_need": {"gratitude": 0.3, "affinity": 0.15, "trust": 0.1},
    "gift": {"gratitude": 0.1, "affinity": 0.08, "trust": 0.03},
    "gave": {"affinity": 0.03},  # the giver warms to the receiver
    "refused_in_need": {"affinity": -0.15, "grievance": 0.2, "trust": -0.05},
    "refused": {"affinity": -0.05, "grievance": 0.05},
    "praised": {"affinity": 0.1},
    "insulted": {"affinity": -0.15, "grievance": 0.1},
    "threatened": {"fear": 0.2, "grievance": 0.2, "affinity": -0.2},
    "robbed": {"fear": 0.1, "grievance": 0.3, "affinity": -0.2, "trust": -0.2},
    "defied": {"respect": 0.1, "grievance": 0.05},  # a threatener's view of someone who refused
    # Families (M6)
    "proposed_to": {"affinity": 0.05, "respect": 0.05},  # being asked is flattering
    "partnered": {"affinity": 0.15, "trust": 0.15, "familiarity": 0.1},
    "declined": {"affinity": -0.05},  # the proposer's view after a "no"
    "fed_by_parent": {"gratitude": 0.1, "affinity": 0.05, "trust": 0.05},
    # Groups and conflict (M7)
    "joined_with": {"affinity": 0.1, "trust": 0.1, "familiarity": 0.05},
    "obeyed": {"respect": 0.05, "affinity": 0.05},  # a leader's view of a member who obeyed
    "disobeyed": {"affinity": -0.1, "grievance": 0.1},
    "backed": {"affinity": 0.05, "trust": 0.05},  # the winner of a challenge, of supporters
    "lost_challenge": {"grievance": 0.2, "respect": 0.05},
    "beaten": {"fear": 0.4, "grievance": 0.2, "affinity": -0.25, "respect": 0.1},
    "won_against": {"grievance": -0.1, "respect": -0.05},
    "saw_attack": {"affinity": -0.1, "fear": 0.1, "trust": -0.1},
    "feud": {"grievance": 0.15, "affinity": -0.1},  # half the grudge, for family and group
    "trespass": {"grievance": 0.06, "affinity": -0.04},  # an outsider in our home area, daily
    "follows": {"respect": 0.25},  # joining a group means accepting its leader
    "taught": {"gratitude": 0.15, "respect": 0.1, "affinity": 0.05},  # M8b
    "traded": {"trust": 0.03, "affinity": 0.02, "familiarity": 0.02},  # M8d
    "promise_kept": {"trust": 0.15, "affinity": 0.05},
    "promise_broken": {"trust": -0.3, "grievance": 0.3, "affinity": -0.15},
    "healed": {"gratitude": 0.2, "affinity": 0.08, "respect": 0.05},
}

# Daily fading (multiplied once per simulated day).
DAILY_DECAY = {"gratitude": 0.9, "grievance": 0.98, "fear": 0.95}


def _clamp(dimension: str, value: float) -> float:
    low = -1.0 if dimension in SIGNED else 0.0
    return round(min(1.0, max(low, value)), 6)


@dataclass
class Relationship:
    target_id: int
    target_name: str
    familiarity: float = 0.0
    affinity: float = 0.0
    trust: float = STRANGER_TRUST
    respect: float = 0.0
    fear: float = 0.0
    gratitude: float = 0.0
    grievance: float = 0.0
    met: bool = True  # False: only heard about them through gossip
    last_event_id: int | None = None
    grudge_event_id: int | None = None  # the event that last deepened the grievance (M7)
    last_chat_day: int = -1
    last_praise_day: int = -1
    last_gossip_day: int = -1
    last_insult_day: int = -1

    # --------------------------------

    def apply(self, changes: dict[str, float], event_id: int | None) -> list[tuple]:
        """Add deltas. Returns [(dimension, old, new)] for the history."""
        history = []
        for dimension, delta in changes.items():
            old = getattr(self, dimension)
            # Feelings get harder to push further the stronger they already are.
            if dimension == "affinity":
                delta *= (1 - old) if delta > 0 else (1 + old)
            elif dimension in ("trust", "familiarity", "gratitude", "grievance", "fear", "respect"):
                delta *= (1 - old) if delta > 0 else 1
            new = _clamp(dimension, old + delta)
            if new != old:
                setattr(self, dimension, new)
                history.append((dimension, old, new))
                if dimension == "grievance" and new > old and event_id is not None:
                    self.grudge_event_id = event_id
        if history and event_id is not None:
            self.last_event_id = event_id
        return history

    def decay(self):
        for dimension, factor in DAILY_DECAY.items():
            setattr(self, dimension, _clamp(dimension, getattr(self, dimension) * factor))

    # --------------------------------

    def labels(self) -> list[str]:
        out = []
        if self.affinity <= -0.6 and self.grievance >= 0.6:
            out.append("enemy")
        elif self.affinity <= -0.3 or self.grievance >= 0.5:
            out.append("rival")
        elif self.affinity >= 0.5 and self.trust >= 0.5:
            out.append("close friend" if self.familiarity >= 0.8 else "friend")
        if self.respect >= 0.7:
            out.append("admires")
        if self.fear >= 0.6:
            out.append("fears")
        return out

    def label(self) -> str:
        """The main label, for display and change events."""
        labels = self.labels()
        if labels:
            return labels[0]
        return "acquaintance" if self.met else "stranger"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Relationship":
        return cls(**data)
