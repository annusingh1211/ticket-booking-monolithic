#!/usr/bin/env python3
"""
TicketFlow deployment script.

Usage:
    python3 deploy/deploy.py

Force rebuild/restart even when the working tree is unchanged:
    FORCE_DEPLOY=1 python3 deploy/deploy.py

Database handling:
- Fresh database: bootstrap current schema/data, then stamp Alembic at head.
- Existing database: run normal Alembic migrations, then idempotent seed.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path


APP_DIR = Path(os.getenv("APP_DIR", Path.home() / "ticket-booking")).expanduser()
BRANCH = os.getenv("BRANCH", "main")
REPO_URL = os.getenv(
    "REPO_URL",
    "https://github.com/DEVENDRA-5470/ticket-booking.git",
)
HEALTH_URL = os.getenv("HEALTH_URL", "http://127.0.0.1/api/health")
FORCE_DEPLOY = os.getenv("FORCE_DEPLOY", "0") == "1"
LOCK_FILE = Path("/tmp/ticketflow-deploy.lock")
LOG_FILE = APP_DIR / "deploy.log"


class DeployError(RuntimeError):
    pass


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
) -> str:
    log(f"$ {' '.join(command)}")

    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=False,
    )

    if check and result.returncode != 0:
        output = result.stdout or ""
        if output:
            print(output, end="", flush=True)

        raise DeployError(
            f"Command failed with exit code {result.returncode}: "
            f"{' '.join(command)}"
        )

    return (result.stdout or "").strip()


def require_commands() -> None:
    for command in ("git", "docker", "curl"):
        result = subprocess.run(
            ["bash", "-lc", f"command -v {command}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise DeployError(f"{command} is not installed")

    run(["docker", "compose", "version"])
    run(["docker", "info"], capture=True)


def acquire_lock():
    lock = LOCK_FILE.open("w")

    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another deployment is already running. Exiting.")
        sys.exit(0)

    return lock


def compose_project_ready() -> bool:
    required = (
        APP_DIR / "docker-compose.yml",
        APP_DIR / "Dockerfile",
        APP_DIR / ".env",
    )
    return all(path.exists() for path in required)


def wait_for_backend() -> None:
    log("Waiting for backend container...")

    for _ in range(30):
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                "ticketing-backend",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        if (
            result.returncode == 0
            and result.stdout.strip() == "true"
        ):
            log("Backend container is running.")
            return

        time.sleep(2)

    run(
        ["docker", "logs", "--tail", "100", "ticketing-backend"],
        check=False,
    )
    raise DeployError("Backend failed to start")


def prepare_database() -> None:
    """Safely initialize or migrate the database."""
    log("Inspecting database state...")

    inspect_script = (
        "from sqlalchemy import inspect; "
        "from app.db.session import engine; "
        "tables=set(inspect(engine).get_table_names()); "
        "print('TABLES=' + ','.join(sorted(tables)))"
    )

    inspection = run(
        ["docker","compose","exec","-T","backend","env","PYTHONPATH=/app",
         "python","-c",inspect_script],
        cwd=APP_DIR,
        capture=True,
    )
    log(inspection)

    app_tables = {"users","events","seats","bookings","booking_seats"}
    tables = set()
    for line in inspection.splitlines():
        if line.startswith("TABLES="):
            tables = {x for x in line.removeprefix("TABLES=").split(",") if x}
            break
    else:
        raise DeployError("Could not determine database schema state")

    if not tables.intersection(app_tables):
        log("Fresh database detected. Bootstrapping schema and seed data...")
        run(
            ["docker","compose","exec","-T","backend","env","PYTHONPATH=/app",
             "python","/app/scripts/create_table_seed_data.py"],
            cwd=APP_DIR,
        )
        log("Stamping freshly bootstrapped schema at Alembic head...")
        run(
            ["docker","compose","exec","-T","backend","alembic","stamp","head"],
            cwd=APP_DIR,
        )
    else:
        log("Existing database detected. Running Alembic migrations...")
        run(
            ["docker","compose","exec","-T","backend","alembic","upgrade","head"],
            cwd=APP_DIR,
        )
        log("Running idempotent seed sync...")
        run(
            ["docker","compose","exec","-T","backend","env","PYTHONPATH=/app",
             "python","/app/scripts/create_table_seed_data.py"],
            cwd=APP_DIR,
        )


def health_check() -> None:
    log("Running application health check...")

    for _ in range(45):
        result = subprocess.run(
            [
                "curl",
                "-fsS",
                "--max-time",
                "5",
                HEALTH_URL,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            log(f"Health check passed: {result.stdout.strip()}")
            return

        time.sleep(2)

    log("Health check failed.")

    run(
        ["docker", "logs", "--tail", "100", "ticketing-backend"],
        check=False,
    )
    run(
        ["docker", "compose", "ps"],
        cwd=APP_DIR,
        check=False,
    )

    raise DeployError("Application health check failed")


def sync_repository() -> None:
    if not (APP_DIR / ".git").exists():
        if APP_DIR.exists() and any(APP_DIR.iterdir()):
            raise DeployError(
                f"{APP_DIR} exists but is not a Git repository"
            )

        log("Repository not found. Cloning repository...")
        APP_DIR.parent.mkdir(parents=True, exist_ok=True)

        run(
            [
                "git",
                "clone",
                "--branch",
                BRANCH,
                REPO_URL,
                str(APP_DIR),
            ]
        )
        return

    run(
        ["git", "remote", "set-url", "origin", REPO_URL],
        cwd=APP_DIR,
    )

    log(f"Fetching latest {BRANCH}...")
    run(
        ["git", "fetch", "--prune", "origin", BRANCH],
        cwd=APP_DIR,
    )


def deploy() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)

    require_commands()
    sync_repository()

    if not (APP_DIR / ".env").exists():
        raise DeployError(f".env not found: {APP_DIR / '.env'}")

    os.chdir(APP_DIR)

    remote_commit = run(
        ["git", "rev-parse", f"origin/{BRANCH}"],
        cwd=APP_DIR,
        capture=True,
    )
    current_commit = run(
        ["git", "rev-parse", "HEAD"],
        cwd=APP_DIR,
        capture=True,
    )

    log(f"Current commit: {current_commit}")
    log(f"Remote commit : {remote_commit}")

    # Normal mode: deploy only when GitHub has a different commit.
    # Force mode: rebuild/restart without needing a new commit.
    if current_commit == remote_commit and not FORCE_DEPLOY:
        log("No new changes. Deployment not required.")
        return

    previous_commit = current_commit

    try:
        log(f"Deploying commit: {remote_commit}")

        if current_commit != remote_commit:
            run(
                ["git", "reset", "--hard", f"origin/{BRANCH}"],
                cwd=APP_DIR,
            )

            run(
                [
                    "git",
                    "clean",
                    "-fd",
                    "-e",
                    ".env",
                    "-e",
                    "deploy.log",
                ],
                cwd=APP_DIR,
            )
        else:
            log("FORCE_DEPLOY=1: keeping current application code.")

        if not compose_project_ready():
            raise DeployError("Required Docker Compose project files are missing")

        log("Validating Docker Compose configuration...")
        run(
            ["docker", "compose", "config"],
            cwd=APP_DIR,
            capture=True,
        )

        log("Building Docker images...")
        run(
            ["docker", "compose", "build", "--pull"],
            cwd=APP_DIR,
        )

        log("Starting backend...")
        run(
            ["docker", "compose", "up", "-d", "backend"],
            cwd=APP_DIR,
        )

        wait_for_backend()

        prepare_database()

        log("Starting complete application...")
        run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--remove-orphans",
            ],
            cwd=APP_DIR,
        )

        health_check()

        deployed_commit = run(
            ["git", "rev-parse", "HEAD"],
            cwd=APP_DIR,
            capture=True,
        )

        log("==========================================")
        log("DEPLOYMENT SUCCESSFUL")
        log(f"Commit: {deployed_commit}")
        log("==========================================")

        run(
            ["docker", "compose", "ps"],
            cwd=APP_DIR,
        )

    except Exception:
        if current_commit != remote_commit and previous_commit:
            log("Deployment failed after a code update; starting rollback.")
            try:
                run(
                    ["git", "reset", "--hard", previous_commit],
                    cwd=APP_DIR,
                )
                run(
                    [
                        "docker",
                        "compose",
                        "up",
                        "-d",
                        "--build",
                        "--remove-orphans",
                    ],
                    cwd=APP_DIR,
                    check=False,
                )
                log("Application rollback completed.")
            except Exception as exc:
                log(f"Rollback failed: {exc}")
        raise


def main() -> int:
    lock = acquire_lock()

    try:
        deploy()
        return 0
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
