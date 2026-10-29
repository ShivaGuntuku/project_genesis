class UtilityScorer:
    """Scores for free-time actions. Personality shapes preferences."""

    def score_explore(self, agent):
        p = agent.genome
        return p.curiosity * 0.6 + p.risk_tolerance * 0.2 + p.ambition * 0.2

    def score_socialize(self, agent):
        p = agent.genome
        return p.sociability * 0.7 + p.generosity * 0.2 + (1 - p.aggression) * 0.1

    def score_rest(self, agent):
        score = 0.3

        if agent.energy < 50:
            score += 0.3

        if agent.health < 60:
            score += 0.3

        return min(score, 1.0)
