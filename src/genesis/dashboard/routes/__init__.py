"""The dashboard's API, one module per page or topic."""

from genesis.dashboard.routes import (
    agents,
    conversations,
    culture,
    events,
    groups,
    lab,
    land,
    live,
    llm,
    network,
    overview,
    runs,
)

MODULES = (
    overview,
    conversations,
    agents,
    llm,
    events,
    groups,
    culture,
    runs,
    land,
    lab,
    network,
    live,
)
