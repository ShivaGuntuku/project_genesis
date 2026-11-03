"""Talking, giving and gossiping between two adjacent agents. See docs/04-society.md (4.1).

Every message is a structured speech act. The simulation acts on the act and its payload.

From M4 a mind (the local LLM) may decide at *decision points*: it chooses one of the options
built here (all valid right now) and says it in its own words. If there is no mind, or it
fails, or the budget is spent, the rules below decide, exactly as in M3.

All checks happen here, so invalid acts can't change the world:
- you can only GIVE what you carry;
- a truthful SHARE_INFO must come from your own memory (otherwise it's a lie, marked as such).
"""

from genesis.society.interaction.basics import (
    NEED_OF,
    RESOURCES,
    Act,
    _ask,
    _asked,
    _can_give,
    _count,
    _lie_chance,
    _nearest,
    _reject,
    _unit,
    feel,
    looks_in_need,
    say,
    think,
)
from genesis.society.interaction.gossip import _gossip_subject, _opinion, gossip
from genesis.society.interaction.info import _fake_location, request_info, share_info
from genesis.society.interaction.items import _escalate, give, request_item, threaten
from genesis.society.interaction.talk import (
    GOAL_ACTS,
    _choose_act,
    _first_contact_today,
    _greet,
    _heard,
    _options,
    _talk_on,
    converse,
    insult,
    perform,
    praise,
)

__all__ = [
    "RESOURCES",
    "NEED_OF",
    "Act",
    "say",
    "think",
    "_reject",
    "feel",
    "looks_in_need",
    "_nearest",
    "_unit",
    "_count",
    "_can_give",
    "_lie_chance",
    "_ask",
    "_asked",
    "share_info",
    "_fake_location",
    "request_info",
    "give",
    "request_item",
    "threaten",
    "_escalate",
    "_opinion",
    "_gossip_subject",
    "gossip",
    "converse",
    "praise",
    "insult",
    "_first_contact_today",
    "_greet",
    "GOAL_ACTS",
    "_choose_act",
    "_options",
    "perform",
    "_talk_on",
    "_heard",
]
