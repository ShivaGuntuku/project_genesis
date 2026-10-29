"""Valley words: names that belong to the valley, not to our world (M8b).

A name is a valley word if it is a single made-up word: not in an English word list, not an
Earth name we know of, and not already used in the valley.
"""

import re
from functools import lru_cache
from pathlib import Path

WORD = re.compile(r"^[A-Za-z]{3,12}$")
DICTIONARIES = (
    Path("/usr/share/dict/american-english"),
    Path("/usr/share/dict/british-english"),
    Path("/usr/share/dict/words"),
)
# Earth names that word lists may lack but the model likes to use (checked lower-case).
EARTH_NAMES = frozenset(
    """torvald thor odin zeus apollo atlantis eden babel noah adam eve jesus christ allah
    buddha krishna rome roma egypt greece china india africa europe asia america london paris
    google amazon tesla iphone excalibur camelot narnia hogwarts gondor mordor""".split()
)
# Words that mean something from our world has leaked in (M8f: much wider). The valley has a
# lake, woods, hills, grassland, berries, clay, reed, fibre, wood, stone, flint, shells and
# bright stones, and people. Nothing else: no animals, no fish, no rivers or sea, no farming,
# no buildings, no metal, no weeks or months (they count days and seasons).
EARTH_TERMS = frozenset(
    """money coin coins cash price city cities town towns village king queen prince lord god gods
    church temple priest pray prayer heaven hell devil phone car cart wagon internet computer
    robot machine engine school book books letter write writing paper doctor medicine police
    soldier army war dollar shop market bronze iron steel metal copper gold silver tin
    wheel bow arrow sword knife knives axe hammer nail pottery potter
    fish fishes fishing fisher bird birds animal animals deer wolf wolves bear bears
    rabbit fox boar cow cows sheep goat goats horse horses dog dogs cat cats chicken pig pigs
    meat milk egg eggs cheese butter bread wheat corn rice grain flour honey salt sugar
    hunt hunting hunter hunters farm farms farming farmer crop crops harvest plough garden
    orchard vineyard wine beer ale tea coffee tobacco
    river rivers stream streams creek sea seas ocean beach boat boats ship ships sail raft fisherman
    house houses hut huts cabin tent tents roof wall walls door doors window bed chair table
    kitchen oven stove fireplace road roads bridge
    week weeks weekend month months monday tuesday wednesday thursday friday saturday sunday
    january february march april june july august september october november december
    clock clocks minute minutes calendar birthday holiday christmas""".split()
)
# Names of our world's made things: an invention described as one of these is our idea,
# not theirs (rejected in invention ideas, counted as leaks in speech).
EARTH_OBJECTS = frozenset(
    """bottle bottles basket baskets bucket buckets bag bags sack sacks pouch purse cup cups mug
    bowl bowls jar jars jug jugs pot pots pan vase urn barrel box boxes crate chest canteen flask
    waterskin waterskins gourd plate plates spoon fork ladle scoop scoops
    spear spears club clubs shield helmet visor armor armour sling slingshot dagger blade blades
    hat hats cap caps headband headpiece crown necklace bracelet ring rings jewel jewelry jewellery
    crutch cane staff rope ropes net nets trap traps hook hooks ladder sled shoe shoes sandal
    sandals boot boots coat cloak shirt dress blanket mat mats rug pillow
    hut shelter""".split()
) - {"shelter"}
# The rules' plain words for things, which the LLM must not just copy (see goals.NEEDS).


def leak_words(text: str) -> list[str]:
    """Words of our world in a text: terms and object names (plurals too)."""
    out = []
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        base = w[:-1] if w.endswith("s") and len(w) > 3 else w
        if w in EARTH_TERMS or w in EARTH_OBJECTS or base in EARTH_TERMS or base in EARTH_OBJECTS:
            out.append(w)
    return out


@lru_cache(maxsize=1)
def dictionary() -> frozenset[str]:
    for path in DICTIONARIES:
        if path.exists():
            words = set()
            for line in path.read_text(errors="ignore").splitlines():
                w = line.strip().lower()
                if w:
                    words.add(w.replace("'s", ""))
            return frozenset(words)
    return frozenset()


def is_valley_word(name: str, taken: set[str] | frozenset[str] = frozenset()) -> bool:
    """A new made-up single word: not English, not an Earth name, not taken."""
    if not WORD.match(name or ""):
        return False
    w = name.lower()
    if w in taken or w in EARTH_NAMES or w in EARTH_TERMS or w in EARTH_OBJECTS:
        return False
    words = dictionary()
    return w not in words and w.rstrip("s") not in words


def leaks(text: str) -> list[str]:
    """Words of our world in a text (for the leak rate and to reject what leaks)."""
    return leak_words(text)
