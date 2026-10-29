from dataclasses import asdict, dataclass


@dataclass
class SocialMemory:
    agent_id: int
    name: str
    last_position: tuple
    first_seen: int
    last_seen: int
    met_event_id: int | None = None
    encounters: int = 1

    def record_encounter(self, position, tick) -> bool:
        """Returns True if this starts a new encounter.

        Seeing someone every hour of one meeting counts once:
        a new encounter starts only after they were out of sight.
        """
        new = tick - self.last_seen > 1
        if new:
            self.encounters += 1

        self.last_position = position
        self.last_seen = tick
        return new

    def describe(self):
        return {
            "id": self.agent_id,
            "name": self.name,
            "last_position": self.last_position,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "encounters": self.encounters,
        }

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "last_position": tuple(data["last_position"])})
