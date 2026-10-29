# Genesis

**A living world that writes its own history.**

Genesis is a local, deterministic simulation of a small society. Its people are agents with genes, needs, skills, memories and relationships. With a local language model (through [Ollama](https://ollama.com)) they think, talk, plan, reflect and set goals in their own words. Nobody scripts what happens: families form, groups choose leaders, quarrels turn into feuds, tools get invented and taught, money emerges from trade, and disasters test whole valleys. Every event is recorded with its cause, so anything that happens can be traced back to why.

The long-term aim is the **Millennium Experiment**: let several valleys run for a thousand simulated years and see whether they grow a culture of their own, with their own words, ideas, beliefs and ways of living, different from ours and from each other.

> Status: the engine, the observer tools and crash-safe long runs are complete. The next phase is about open-ended invention (tools that build on tools, fire, farming, building) and a land that changes over time.

## What it does

- **Agents with their own minds:** a genome (personality, body, rhythm), needs, skills, talents, memories with recall, nightly reflection, beliefs, a self-story, goals and plans. Without an LLM, the rules decide everything.
- **Society:** conversations and speech acts (sharing, gifts, lies, gossip, threats), relationships with several feelings, families and inheritance, groups with leaders, orders and challenges, fights and territory.
- **Culture:** materials with hidden physics, invention and teaching, names in the valley's own made-up words, barter, promises, and money that may emerge.
- **A millennium engine:** years and seasons, sleep shifts, generated valleys with ridges and passes, staged growth, disasters every 15–25 years, and migration.
- **No words from our world:** what the agents say, believe and invent is checked so that it stays inside their world.
- **Long runs that survive anything:** daily transactional saves, every LLM answer journaled on arrival, automatic snapshots and integrity checks. A run resumes after a crash or `kill -9` with exactly the same history.
- **Observe and experiment:** a live pixel-art viewer, an observer dashboard (world map with past years, people, conversations, ideas, economy, family tree, mind lab, interviews, God Mode), branching and comparing worlds, and an experiment runner across seeds.

## System requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Linux or macOS (uses `fcntl` and POSIX signals; Windows: use WSL2) | Ubuntu 22.04+ |
| Python | 3.12 | 3.12, with [uv](https://docs.astral.sh/uv/) |
| Node.js | 20 (to build the viewer and dashboard) | 22+ |
| CPU and RAM | 4 cores, 8 GB (rules only) | 8+ cores, 16 GB |
| GPU (for the LLM mind) | none (rules only; or a CPU-only LLM, slow) | NVIDIA with 8 GB+ VRAM; 24 GB for many parallel calls |
| Ollama | 0.9+, with `llama3` and `nomic-embed-text` | the same; or any OpenAI-compatible server (vLLM, llama.cpp `llama-server`, LM Studio) |
| Disk | 1 GB | 10 GB+ (models about 5 GB; a long run's world file is 100–800 MB) |

## Install

```bash
git clone https://github.com/ShivaGuntuku/project_genesis.git genesis
cd genesis
uv sync                                    # Python dependencies (into .venv)
(cd ui && npm install && npm run build)    # the live viewer
(cd dashboard && npm install && npm run build)   # the observer dashboard

# Optional: the LLM mind
ollama pull llama3
ollama pull nomic-embed-text
uv run genesis bench-llm --models llama3 --workers 4   # measure, then set [llm] limits
```

## Run

```bash
# Create a world (worlds/*.toml are ready-made presets)
uv run genesis new --config worlds/millennium.toml --name myworld           # rules only
uv run genesis new --config worlds/millennium.toml --name myworld --llm     # with the LLM mind
uv run genesis worlds

# Watch it live in the browser (the viewer runs the world itself): http://127.0.0.1:8800
uv run genesis view myworld

# The observer dashboard (read-only, safe at any time): http://127.0.0.1:8810
uv run genesis dashboard myworld

# Run in the terminal
uv run genesis run myworld --days 360
```

### Long runs

```bash
uv run genesis start myworld --years 100      # saves daily, journals LLM answers, takes snapshots
uv run genesis runs                           # what every long run is doing
uv run genesis pause | continue | stop myworld
uv run genesis resume myworld                 # after any stop, crash or reboot: same history
uv run genesis service install myworld        # optional systemd user service (Linux)
```

Only one process may run a world at a time (a lock refuses a second one). The dashboard only reads, so it is safe while a run is going.

### Inspect, experiment, intervene

```bash
uv run genesis check myworld                  # grades the world against the project's goals
uv run genesis status myworld                 # also: inspect, events, why, thoughts, memories, family, metrics
uv run genesis replay myworld                 # re-run from recorded answers: identical?
uv run genesis experiment worlds/millennium.toml --seeds 1-4 --years 25 --arm "no trade: trade=0"
uv run genesis branch myworld test            # copy a world, then change it:
uv run genesis god test disaster drought all 90
uv run genesis compare myworld test
```

## World presets

| File | What |
|---|---|
| `worlds/eden.toml` | a small story world (hours, a handful of people) |
| `worlds/two_lakes.toml` | two lakes; a scheduled drought (groups and conflict) |
| `worlds/two_valleys.toml` | two hand-made valleys, years and seasons |
| `worlds/millennium.toml` | three generated valleys (100×100), disasters, growth towards 50 people |
| `worlds/pilot_p*.toml` | pilot runs (10 and 100 years, isolated valleys, rules-only control) |

Every setting (map, population, rules, time, LLM) lives in the TOML file.

## Project layout

```text
src/genesis/
  agents/       genome, abilities, talents, lifecycle, the agent
  mind/         perception, decisions, planning, memory, goals, plans
  society/      conversation, relationships, families, groups, conflict, economy
  culture/      materials and hidden physics, inventions, valley words
  world/        the world, generated maps, disasters
  simulation/   engine, calendar, long-run service, experiments
  llm/          gateways (Ollama, OpenAI-compatible), prompts, persona, reflection
  persistence/  the SQLite world file
  history/      events, descriptions, metrics, the check
  god/          God Mode, branching and comparing
  api/ dashboard/   viewer and dashboard servers
ui/             live viewer (Phaser, TypeScript)
dashboard/      observer dashboard (React, ECharts, TypeScript)
worlds/         world presets
scripts/        crash-safety check, pilot runner
tests/          pytest suite
```

## Development

```bash
uv run pytest -q                       # the test suite (about 300 tests)
uv run ruff check src tests            # lint
uv run python scripts/kill_test.py --kills 20 --days 360   # crash-safety check
```

Simulations are deterministic: the same seed, settings and recorded LLM answers always give the same history.

## License

[MIT](LICENSE) © 2026 ShivaPrasad Guntuku
