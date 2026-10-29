"""How the agents' communication changes over time, measured from every message.

Kept in memory and updated incrementally (only messages newer than the last update are
read), so it stays cheap on long runs. Words come from lines spoken in the agents' own
words (the LLM mind); rule-made acts have no words.

"Valley words" are words that are neither English (system word list) nor anyone's name:
the first place where a language of their own could show up.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
STOPWORDS = frozenset(
    """a an the to of and or in on at for with is are was were be been being i you me my mine
    your yours it its that this these those there they them their he she him her his hers we
    us our from as by not no so do does did some any what where who whom which when why how
    said have has had will would can could just but if then than too very also all about
    up out into over i'm i'll i've you're it's let's don't can't that's there's here
    yes oh well am get got go going""".split()
)
CONTRACTIONS = ("n't", "'re", "'ll", "'ve", "'d", "'m", "'s")
DICTIONARIES = (
    Path("/usr/share/dict/american-english"),
    Path("/usr/share/dict/british-english"),
    Path("/usr/share/dict/words"),
)


def load_dictionary() -> set[str] | None:
    for path in DICTIONARIES:
        if path.exists():
            words = set()
            for line in path.read_text(errors="ignore").splitlines():
                w = line.strip().lower()
                if w:
                    words.add(w.replace("'s", ""))
            return words
    return None


def words_of(line: str) -> list[str]:
    return WORD.findall(line.lower())


@dataclass
class Day:
    messages: int = 0
    voiced: int = 0
    lies: int = 0
    words: int = 0
    threads: set = field(default_factory=set)
    pairs: set = field(default_factory=set)
    speakers: set = field(default_factory=set)
    acts: Counter = field(default_factory=Counter)
    answers: Counter = field(default_factory=Counter)
    vocabulary: Counter = field(default_factory=Counter)
    new_words: int = 0


class LanguageStats:
    def __init__(self, calendar, dictionary: set[str] | None):
        """calendar: a Calendar, or (older callers) the start hour of an hourly world."""
        if isinstance(calendar, int):
            from genesis.simulation.calendar import Calendar

            calendar = Calendar(start_hour=calendar)
        self.calendar = calendar
        self.dictionary = dictionary
        self.last_event_id = 0
        self.days: dict[int, Day] = {}
        self.first_use: dict[str, dict] = {}  # word -> {day, tick, speaker, event_id, line}
        self.counts: Counter = Counter()

    def day_of(self, tick: int) -> int:
        return self.calendar.day(tick)

    def update(self, conn):
        rows = conn.execute(
            "SELECT event_id, tick, from_id, to_id, act, payload_json, truthful, line "
            "FROM messages WHERE event_id > ? ORDER BY event_id",
            (self.last_event_id,),
        ).fetchall()
        for r in rows:
            self._add(r)
            self.last_event_id = r["event_id"]
        return len(rows)

    def _add(self, r):
        day = self.days.setdefault(self.day_of(r["tick"]), Day())
        lo, hi = sorted((r["from_id"], r["to_id"]))
        day.messages += 1
        day.threads.add((r["tick"], lo, hi))
        day.pairs.add((lo, hi))
        day.speakers.add(r["from_id"])
        day.acts[r["act"]] += 1
        if not r["truthful"]:
            day.lies += 1
        if r["act"] == "reply":
            answer = json.loads(r["payload_json"]).get("answer")
            if answer:
                day.answers[answer] += 1
        line = r["line"]
        if not line:
            return
        day.voiced += 1
        words = words_of(line)
        day.words += len(words)
        for w in words:
            day.vocabulary[w] += 1
            self.counts[w] += 1
            if w not in self.first_use:
                day.new_words += 1
                self.first_use[w] = {
                    "word": w,
                    "day": self.day_of(r["tick"]),
                    "tick": r["tick"],
                    "speaker": r["from_id"],
                    "eventId": r["event_id"],
                    "line": line,
                }

    # --------------------------------

    def series(self) -> list[dict]:
        out, vocabulary = [], 0
        for number in sorted(self.days):
            d = self.days[number]
            vocabulary += d.new_words
            threads = len(d.threads)
            out.append(
                {
                    "day": number,
                    "messages": d.messages,
                    "conversations": threads,
                    "turnsPerConversation": round(d.messages / threads, 2) if threads else 0,
                    "voicedShare": round(d.voiced / d.messages, 3) if d.messages else 0,
                    "wordsPerLine": round(d.words / d.voiced, 2) if d.voiced else 0,
                    "distinctWords": len(d.vocabulary),
                    "newWords": d.new_words,
                    "vocabulary": vocabulary,
                    "lieRate": round(d.lies / d.messages, 3) if d.messages else 0,
                    "pairs": len(d.pairs),
                    "speakers": len(d.speakers),
                    "acts": dict(d.acts),
                    "answers": dict(d.answers),
                }
            )
        return out

    def words(self, names: set[str], recent_days: int = 7) -> dict:
        names = {n.lower() for n in names}
        meaningful = {w: c for w, c in self.counts.items() if w not in STOPWORDS and len(w) > 2}
        top = sorted(meaningful.items(), key=lambda kv: (-kv[1], kv[0]))[:40]

        last = max(self.days) if self.days else 0
        recent, before = Counter(), Counter()
        for number, d in self.days.items():
            (recent if number > last - recent_days else before).update(d.vocabulary)
        recent_total = sum(recent.values()) or 1
        before_total = sum(before.values()) or 1
        rising = []
        for w, c in recent.items():
            if w in STOPWORDS or len(w) < 3 or c < 3:
                continue
            share_now = c / recent_total
            share_before = before.get(w, 0) / before_total
            rising.append((share_now / (share_before + 1 / before_total), w, c))
        rising.sort(reverse=True)

        newest = [
            u
            for u in sorted(self.first_use.values(), key=lambda u: -u["eventId"])
            if u["word"] not in STOPWORDS and len(u["word"]) > 2
        ][:40]

        valley = []
        if self.dictionary is not None:
            for w, c in self.counts.items():
                if w in self.dictionary or w.endswith(CONTRACTIONS):
                    continue
                base = w.split("'")[0]
                if len(base) < 3 or base in names or base in STOPWORDS:
                    continue
                if base in self.dictionary or base.rstrip("s") in self.dictionary:
                    continue
                valley.append({**self.first_use[w], "count": c})
            valley.sort(key=lambda u: (-u["count"], u["eventId"]))

        return {
            "totalWords": sum(self.counts.values()),
            "vocabulary": len(self.counts),
            "top": [{"word": w, "count": c} for w, c in top],
            "rising": [{"word": w, "count": c, "ratio": round(r, 1)} for r, w, c in rising[:20]],
            "newest": newest,
            "valley": valley[:60],
            "dictionary": self.dictionary is not None,
        }
