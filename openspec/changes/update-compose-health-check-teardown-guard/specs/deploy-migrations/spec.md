## ADDED Requirements

### Requirement: compose-health-check teardown steps SHALL guard against .env.ci never being generated

The `compose-health-check` job's `Migration summary` and `Cleanup` steps both run with `if: always()` and both read or pass `.env.ci`. Because the job runs under a `cancel-in-progress: true` concurrency group, a new push to the same PR can cancel an in-flight run before its "Generate .env.ci from secrets" step executes. Each of these two steps SHALL check `.env.ci` exists as the first action in its `run:` block and, if absent, print a skip message and exit 0 instead of proceeding — so an early cancellation reports as a normal canceled run instead of stacking `grep: .env.ci: No such file or directory` / `couldn't find env file` errors on top of it.

#### Scenario: Run is canceled before .env.ci is generated

- **GIVEN** a `compose-health-check` run is canceled (a newer push to the same PR triggers `cancel-in-progress: true`) before the "Generate .env.ci from secrets" step runs
- **WHEN** the `Migration summary` and `Cleanup` steps evaluate their `if: always()` condition and run
- **THEN** each step SHALL detect `.env.ci` is absent and exit 0 with a skip message
- **AND** neither step SHALL fail on a missing-file error

#### Scenario: Run completes normally after .env.ci exists

- **GIVEN** a `compose-health-check` run reaches the "Generate .env.ci from secrets" step and `.env.ci` exists on disk
- **WHEN** the `Migration summary` and `Cleanup` steps run
- **THEN** both steps SHALL behave exactly as before the guard was added — `Migration summary` SHALL write migration state to `$GITHUB_STEP_SUMMARY` and `Cleanup` SHALL run `docker compose --env-file .env.ci down -v`

#### Scenario: A genuine failure after .env.ci exists still runs both steps

- **GIVEN** `.env.ci` was generated and a later step (e.g. "Apply database migrations") fails
- **WHEN** the `Migration summary` and `Cleanup` steps run under `if: always()`
- **THEN** the guard SHALL pass (the file exists) and both steps SHALL run their normal logic, preserving the existing failure-diagnostics behavior
