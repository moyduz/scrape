import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

EXCLUDE_NAMES = {
    ".git",
    ".astro",
    "node_modules",
    "dist",
    ".DS_Store",
}


def run(cmd: list[str], cwd: str | Path | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def ensure_git_repo(repo_dir: str | Path, remote_url: str | None = None) -> Path:
    repo = Path(repo_dir).expanduser().resolve()
    repo.mkdir(parents=True, exist_ok=True)

    if not (repo / ".git").exists():
        run(["git", "init"], cwd=repo)

    if remote_url:
        remotes = run(["git", "remote"], cwd=repo).splitlines()
        if "origin" in remotes:
            current = run(["git", "remote", "get-url", "origin"], cwd=repo)
            if current != remote_url:
                run(["git", "remote", "set-url", "origin", remote_url], cwd=repo)
        else:
            run(["git", "remote", "add", "origin", remote_url], cwd=repo)

    return repo


def clean_repo_worktree(repo: Path) -> None:
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def copy_project(source_dir: str | Path, repo: Path, exclude: Iterable[str] = EXCLUDE_NAMES) -> None:
    source = Path(source_dir).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Generated project not found: {source}")
    if source == repo:
        raise ValueError("source_dir and repo_dir cannot be the same")

    for item in source.iterdir():
        if item.name in exclude:
            continue
        dest = repo / item.name
        if item.is_dir():
            shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*exclude))
        else:
            shutil.copy2(item, dest)


def has_changes(repo: Path) -> bool:
    return bool(run(["git", "status", "--porcelain"], cwd=repo))


def deploy_to_git_branch(
    *,
    source_dir: str | Path,
    repo_dir: str | Path,
    branch: str,
    remote_url: str | None = None,
    commit_message: str | None = None,
    push: bool = False,
) -> dict:
    repo = ensure_git_repo(repo_dir, remote_url=remote_url)
    run(["git", "checkout", "-B", branch], cwd=repo)
    clean_repo_worktree(repo)
    copy_project(source_dir, repo)

    run(["git", "add", "-A"], cwd=repo)
    committed = False
    commit_hash = None

    if has_changes(repo):
        run(["git", "commit", "-m", commit_message or f"Deploy {branch}"], cwd=repo)
        committed = True
        commit_hash = run(["git", "rev-parse", "HEAD"], cwd=repo)

    pushed = False
    if push:
        run(["git", "push", "-u", "origin", branch, "--force-with-lease"], cwd=repo)
        pushed = True

    return {
        "repo_dir": str(repo),
        "branch": branch,
        "committed": committed,
        "commit_hash": commit_hash,
        "pushed": pushed,
    }
