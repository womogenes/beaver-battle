# Beaver Battle coordination

Read PLAN.md, DECISIONS.md, and PROTOCOL.md before changing a subsystem. The conversation decisions in DECISIONS.md resolve contradictions in the original brief. All requested game features remain in scope.

- Python for the laptop; C with ESP-IDF for firmware. No `from __future__ import annotations`; do not give custom functions or variables leading underscores. Standard Python filenames and generated dataclass methods are fine.
- Follow https://github.com/DietrichGebert/ponytail: reuse existing code and standard libraries, keep dependencies and abstractions small, and leave a small runnable check for nontrivial logic. Hardware calibration and requested functionality must not be omitted.
- One Python process, a latest-frame vision worker, nonblocking UDP, and a fixed-step game loop. No web services or message broker for the core game.
- Root/integrator owns shared contracts, configuration, launcher, networking, README, and git integration. Vision owner edits vision.py and its check. Game owner edits game.py and its check. Firmware owner edits firmware/ and firmware documentation.
- Do not edit another owner's files without coordinating. Contract changes go through the integrator. Do not commit another worker's unfinished changes.
- Before a checkpoint: run the relevant small checks, inspect the diff, commit specific files, fetch origin, integrate any remote work without discarding it, then push the current branch. Never force-push or reset other contributors' work.
- Keep credentials, local network secrets, calibration captures, build outputs, and machine-specific configuration out of git. Put overrides in ignored config.local.toml or environment variables.
- Record tested behavior and hardware limitations honestly. A simulator pass is not a physical hardware pass.

The existing Onshape document is unrelated to this game implementation. Do not change CAD data as part of this task.
