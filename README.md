# Beaver Battle

A projection-mapped canoe battle for up to three red-laser controllers. Python handles the game, USB camera, calibration, and UDP networking; ESP-IDF C handles two buttons, laser output, and servo feedback.

Implementation is in progress. `PLAN.md` is the original brief; `DECISIONS.md` records agreed changes and subsystem contracts. `PROTOCOL.md` defines the controller wire format. Read `AGENTS.md` before contributing.

## Setup

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run `uv sync --python 3.12`. Configuration is in `config.toml`; put local overrides in ignored `config.local.toml`. The projector address is `projector.ip`, currently `10.31.171.250`.

The intended launcher is `uv run python -m beaver_battle`. A simulation mode will allow development without controllers or projection hardware. See subsequent checkpoints for the runnable launcher and checks.

## Contributing

Clone the existing remote, work on a topic branch, and keep subsystem changes within the ownership boundaries in AGENTS.md. Run the relevant checks before committing. Fetch before pushing and integrate remote changes without force-pushing or discarding another contributor's work. Commit only the files belonging to your completed change.

Do not commit Wi-Fi credentials, camera captures, calibration files, local overrides, or firmware build outputs. Controller power, cooling, and the physical squirter are being built separately by the team.
