# ADR-0004: Windows-First Cross-Platform OS Abstraction Layer

- **ID:** ADR-0004
- **Date:** 2026-10-30
- **Status:** Accepted (`§13.21`, `WIN-01..23`, `D-16`, `D-17`)

## Context

The customer's primary workstation runs Windows, while CI and containerized deployments also run on
Linux (`ubuntu-latest` + `windows-latest`). POSIX-only primitives (`resource.setrlimit`, `os.killpg`,
`SIGTERM`/`SIGKILL`, `bin/python`, directory `fsync`, case-sensitive paths) fail or misbehave on Windows.

## Decision

Provide a dedicated platform abstraction module inside `orchestra/verify/` and `orchestra/state/`
dispatched automatically by `sys.platform`:
- **Resource limits & process trees:** `WindowsResourceLimiter` (Win32 Job Objects via `ctypes`:
  memory cap, active process cap, `KILL_ON_JOB_CLOSE`) and `PosixResourceLimiter` (`resource.setrlimit`
  + `start_new_session=True` + `os.killpg`).
- **Signals & event loop:** Enforce `ProactorEventLoop` on Windows (`WIN-04`); support graceful
  shutdown via `SIGBREAK` / console ctrl handler (`WIN-05`); abstract `terminate_process_tree` and
  `hard_kill` (`WIN-02`, `WIN-06`).
- **Filesystem:** Bounded exponential-backoff retries on `os.replace` `PermissionError` (`WIN-08`),
  canonical case collision check via `os.scandir` raising `CaseCollisionError` (`WIN-10`), `\n` line
  endings everywhere (`WIN-11`), path length warning at `240` chars + `\\?\` prefix (`WIN-12`), and
  explicit `UTF-8` (`PYTHONUTF8=1`, `errors="replace"`, `WIN-13`).
- **Virtualenvs & Docker:** Resolve `Scripts\python.exe` vs `bin/python` via `venv_python()`,
  translate `C:\...` to `/mnt/c/...` via `to_container_path()`, and degrade `docker` sandbox mode
  gracefully to `subprocess` when Docker Desktop / WSL2 is absent (`WIN-20`).

## Rationale

Eliminates platform-specific crashes without leaking `if sys.platform == "win32"` checks across
higher orchestration layers.

## Alternatives Considered

- Require WSL2 for running ORCHESTRA itself: rejected because native Windows execution was chosen
  as primary target (`D-16`).

## Consequences

- `orchestra doctor` exposes a comprehensive Russian "Платформа" readiness report (`WIN-22`).
- CI runs both `ubuntu-latest` and `windows-latest` (`TT-06`).
