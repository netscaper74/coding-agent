"""Repository sources: local folders (sample repos) and GitLab projects (read-only API)."""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target", ".venv", "venv", "__pycache__", ".idea"}


@dataclass
class SourceFile:
    path: str          # repo-relative, forward slashes
    fingerprint: str   # blob sha / content hash; unchanged fingerprint => skip
    size: int


class RepoSource:
    name: str
    branch: str
    commit: str = "unknown"

    def list_files(self) -> Iterator[SourceFile]:
        raise NotImplementedError

    def read(self, f: SourceFile) -> bytes:
        raise NotImplementedError

    def web_url(self, path: str, start: int | None = None, end: int | None = None) -> str | None:
        return None


def _wanted(path: str, extensions: set[str]) -> bool:
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    return any(path.lower().endswith(ext) for ext in extensions)


class LocalSource(RepoSource):
    def __init__(self, name: str, root: str, branch: str, extensions: set[str], max_bytes: int):
        self.name, self.root, self.branch = name, Path(root), branch
        self.extensions, self.max_bytes = extensions, max_bytes
        if not self.root.exists():
            raise FileNotFoundError(f"Local repo path not found: {root}")
        self.commit = self._git_head() or "local"

    def _git_head(self) -> str | None:
        if not (self.root / ".git").exists():
            return None
        try:
            return subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()
        except Exception:
            return None

    def list_files(self) -> Iterator[SourceFile]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                full = Path(dirpath) / fn
                rel = full.relative_to(self.root).as_posix()
                if not _wanted(rel, self.extensions):
                    continue
                data = full.read_bytes()
                if len(data) > self.max_bytes:
                    continue
                yield SourceFile(rel, hashlib.sha1(data).hexdigest(), len(data))

    def read(self, f: SourceFile) -> bytes:
        return (self.root / f.path).read_bytes()


class GitLabSource(RepoSource):
    """Reads a GitLab project through the REST API using a read_repository / read_api token."""

    def __init__(self, name: str, base_url: str, token: str, project: str, branch: str | None,
                 extensions: set[str], max_bytes: int):
        if not base_url or not token:
            raise ValueError(f"GITLAB_URL and GITLAB_TOKEN are required for GitLab repo '{name}'")
        self.name, self.extensions, self.max_bytes = name, extensions, max_bytes
        self.api = f"{base_url.rstrip('/')}/api/v4"
        self.session = requests.Session()
        self.session.headers["PRIVATE-TOKEN"] = token
        info = self._get(f"/projects/{quote(str(project), safe='')}").json()
        self.project_id = info["id"]
        self.project_web_url = info["web_url"]
        self.branch = branch or info.get("default_branch") or "main"
        branch_info = self._get(f"/projects/{self.project_id}/repository/branches/{quote(self.branch, safe='')}").json()
        self.commit = branch_info["commit"]["id"]

    def _get(self, path: str, **params) -> requests.Response:
        resp = self.session.get(self.api + path, params=params, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"GitLab GET {path} failed ({resp.status_code}): {resp.text[:200]}")
        return resp

    def list_files(self) -> Iterator[SourceFile]:
        page = "1"
        while page:
            resp = self._get(f"/projects/{self.project_id}/repository/tree",
                             recursive="true", ref=self.commit, per_page=100, page=page)
            for item in resp.json():
                if item["type"] == "blob" and _wanted(item["path"], self.extensions):
                    yield SourceFile(item["path"], item["id"], -1)
            page = resp.headers.get("X-Next-Page", "")

    def read(self, f: SourceFile) -> bytes:
        data = self._get(f"/projects/{self.project_id}/repository/blobs/{f.fingerprint}/raw").content
        return data if len(data) <= self.max_bytes else b""

    def web_url(self, path: str, start: int | None = None, end: int | None = None) -> str | None:
        anchor = f"#L{start}-{end}" if start and end else ""
        return f"{self.project_web_url}/-/blob/{self.commit}/{quote(path)}{anchor}"


def build_source(repo: dict, settings, extensions: set[str], max_bytes: int) -> RepoSource:
    kind = repo.get("source", "local")
    if kind == "local":
        return LocalSource(repo["name"], repo["path"], repo.get("branch", "main"), extensions, max_bytes)
    if kind == "gitlab":
        return GitLabSource(repo["name"], repo.get("gitlab_url") or settings.gitlab_url,
                            repo.get("token") or settings.gitlab_token, repo["project"],
                            repo.get("branch"), extensions, max_bytes)
    raise ValueError(f"Unknown source type '{kind}' for repo {repo.get('name')}")
