"""Gossip: telling one person what you think of another."""

from genesis.society.interaction.basics import Act, feel, say
from genesis.society.relationships import Relationship


def _opinion(rel: Relationship) -> float:
    """How good (+) or bad (-) the speaker thinks someone is."""
    return rel.affinity + 0.5 * rel.gratitude - 0.7 * rel.grievance


def _gossip_subject(speaker, listener):
    """The third person the speaker feels most strongly about (if strongly enough)."""
    best, strength = None, 0.3
    for target_id in sorted(speaker.relationships):
        if target_id in (listener.id, speaker.id):
            continue
        opinion = _opinion(speaker.relationships[target_id])
        if abs(opinion) >= strength:
            best, strength = target_id, abs(opinion)
    return best


def gossip(ctx, speaker, listener, subject_id, reply_to=None, voice=None):
    """Tell the listener what you think of someone. The listener believes it as much as they
    trust you, and may now like or distrust someone they have never met."""
    subject_rel = speaker.relationships[subject_id]
    opinion = _opinion(subject_rel)
    sign = 1 if opinion > 0 else -1
    claim = (
        "is kind"
        if sign > 0
        else ("can't be trusted" if subject_rel.grievance >= 0.3 else "is unpleasant")
    )
    event_id = say(
        ctx,
        speaker,
        listener,
        Act.GOSSIP,
        {
            "about": subject_id,
            "about_name": subject_rel.target_name,
            "claim": claim,
            "sentiment": sign,
        },
        reply_to=reply_to,
        voice=voice,
    )

    speaker.relationship(listener.id, listener.name).last_gossip_day = ctx.day
    belief = listener.relationship(speaker.id, speaker.name).trust
    subject = ctx.agents.get(subject_id)
    if subject is not None:
        effect = ctx.world.rules.gossip_effect
        changes = {"affinity": effect * sign * belief, "trust": 0.7 * effect * sign * belief}
        feel(ctx, listener, subject, changes, event_id, met=listener.memory.knows_agent(subject_id))
    return event_id
