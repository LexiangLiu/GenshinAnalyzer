# Architecture and Compatibility

## Dependency direction

```text
tools / acceptance UI / CLI
        |
        v
applications
        |
        v
planning_v2 / plans / goals / account / activities / resources
        |
        v
optimization / planning / damage / inventory / data / core
```

The `core`, `data`, `damage`, `inventory`, `optimization`, and `planning` packages must remain independent of HTTP servers, HTML, browser APIs, and acceptance-service temporary directories. Persistence and HTTP orchestration belong in `applications` and `tools`.

## Compatibility boundary

- `artifact_sim` remains the public compatibility facade.
- Existing CLI commands and JSON result shapes remain supported unless an explicit migration is documented.
- Old inventory SQLite tables remain compatible through additive schema migrations.
- Account snapshots, goals, plans, activities, and coverage reports carry `schema_version`.
- Unsupported schema versions must raise an explicit error; they must not be silently interpreted.
- New modules may be added, but existing public imports must remain available.

## Provenance and downgrade policy

- Formula results use the pinned catalog and expose calibration/coverage metadata.
- Uncalibrated formulas are excluded from composite scoring.
- Offline game data is identified by manifest game version and SHA-256 values.
- Activity rules require source, validity range, verification, and confidence for reliable benefits.
- Missing, expired, unverified, or low-confidence data is reported as unknown or degraded.
- Real account synchronization and live activity fetching are not implemented.

## Persistence

- Snapshot, goal, plan, and cache databases use explicit SQLite transactions and `busy_timeout`.
- State updates create new snapshots instead of silently replacing old account snapshots.
- Goal or snapshot changes invalidate associated calculation cache entries.
- Backups use SQLite backup API, SHA-256 manifests, and `quick_check`.
- Restore requires explicit replacement and only replaces validated database files.

## Validation

Run:

```powershell
python tools/run_acceptance_suite.py
python tools/run_preflight.py
python tools/benchmark_growth.py --repeats 5
```
