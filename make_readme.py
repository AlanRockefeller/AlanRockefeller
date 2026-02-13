#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

API = "https://api.github.com"
GQL = "https://api.github.com/graphql"

# ---- Customize these -------------------------------------------------

# Path to your hero image *inside this repo* (relative path).
# Example: "hero.png" or "assets/hero.jpg"
HERO_IMAGE_PATH = "hero.jpg"

# Your preferred (featured) repos, in the order you want them shown.
PREFERRED_REPOS = [
    "inat.label.py",
    "inat.finder.py",
    "faststack",
    "inat.nearbyobservations.py",
    "stackcopy",
    "motoinat.py",
]

WHAT_I_DO_BULLETS = [
    "🧬 DNA barcoding workflows (field → lab → sequences → IDs)",
    "📷 Field photography + automation pipelines for large datasets",
    "🔬 Fungal microscopy",
]

# ---------------------------------------------------------------------


def _headers(token: Optional[str]) -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-generator",
    }
    if token:
        t = token.strip()
        # Refuse tokens containing whitespace/newlines (often CLI error output)
        if any(c.isspace() for c in t):
            return h
        h["Authorization"] = f"Bearer {t}"
    return h


def _get_json(url: str, token: Optional[str], params: Optional[dict] = None) -> Any:
    r = requests.get(url, headers=_headers(token), params=params, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {url} failed: {r.status_code}\n{r.text[:500]}")
    return r.json()


def _post_gql(query: str, token: Optional[str], variables: Optional[dict] = None) -> Any:
    """
    GraphQL via requests. Requires a usable token; otherwise returns None.
    """
    if not token:
        return None
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    r = requests.post(
        GQL,
        headers=_headers(token),
        json=payload,
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"POST GraphQL failed: {r.status_code}\n{r.text[:500]}")
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data")


def _gh_graphql_inline(query: str) -> Optional[dict]:
    """
    Run an inline GraphQL query via GitHub CLI (uses `gh auth login` credentials).
    """
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None

    if proc.returncode != 0:
        return None

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def fetch_user(username: str, token: Optional[str]) -> dict:
    return _get_json(f"{API}/users/{username}", token)


def fetch_repos(username: str, token: Optional[str]) -> List[dict]:
    # Pull up to 200 repos (2 pages).
    repos: List[dict] = []
    for page in (1, 2):
        batch = _get_json(
            f"{API}/users/{username}/repos",
            token,
            params={
                "per_page": 100,
                "page": page,
                "sort": "pushed",
                "direction": "desc",
                "type": "owner",
            },
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
    return repos


def fetch_pinned_repos(username: str, token: Optional[str]) -> List[dict]:
    """
    Try pinned repos via:
      1) requests GraphQL (token-based) if GITHUB_TOKEN is set
      2) gh api graphql inline fallback (uses gh auth login)
    """
    query_vars = """
    query($login: String!) {
      user(login: $login) {
        pinnedItems(first: 6, types: REPOSITORY) {
          nodes {
            ... on Repository {
              name
              url
              description
              stargazerCount
              forkCount
              updatedAt
              primaryLanguage { name }
            }
          }
        }
      }
    }
    """

    data = None
    try:
        data = _post_gql(query_vars, token, {"login": username})
    except Exception:
        data = None

    if not data:
        # Inline query avoids gh variable quirks
        query_inline = f"""
        {{
          user(login:"{username}") {{
            pinnedItems(first:6, types:REPOSITORY) {{
              nodes {{
                ... on Repository {{
                  name
                  url
                  description
                  stargazerCount
                  forkCount
                  updatedAt
                  primaryLanguage {{ name }}
                }}
              }}
            }}
          }}
        }}
        """.strip()
        gh_resp = _gh_graphql_inline(query_inline)
        if gh_resp and isinstance(gh_resp, dict):
            data = gh_resp.get("data")

    if not data:
        return []

    nodes = (((data.get("user") or {}).get("pinnedItems") or {}).get("nodes")) or []

    pinned: List[dict] = []
    for n in nodes:
        pinned.append(
            {
                "name": n.get("name"),
                "html_url": n.get("url"),
                "description": n.get("description") or "",
                "stargazers_count": n.get("stargazerCount") or 0,
                "forks_count": n.get("forkCount") or 0,
                "language": (n.get("primaryLanguage") or {}).get("name") or "",
                "pushed_at": n.get("updatedAt") or "",
                "updated_at": n.get("updatedAt") or "",
                "fork": False,
                "archived": False,
            }
        )
    return pinned


def _is_good_repo(r: dict) -> bool:
    return not r.get("fork") and not r.get("archived")


def _clean_desc(desc: str, max_len: int = 90) -> str:
    d = " ".join(desc.strip().split())
    if len(d) <= max_len:
        return d
    return d[: max_len - 1].rstrip() + "…"


def curated_then_top(repos: List[dict], n: int = 6) -> List[dict]:
    by_name = {str(r.get("name", "")).lower(): r for r in repos}
    picked: List[dict] = []

    for name in PREFERRED_REPOS:
        r = by_name.get(name.lower())
        if r and _is_good_repo(r):
            picked.append(r)

    remaining = [r for r in repos if _is_good_repo(r) and r not in picked]
    remaining.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
        reverse=True,
    )
    return (picked + remaining)[:n]


def pick_recent_repos(repos: List[dict], n: int = 6) -> List[dict]:
    repos_sorted = sorted(repos, key=lambda r: r.get("pushed_at", ""), reverse=True)
    out: List[dict] = []
    for r in repos_sorted:
        if not _is_good_repo(r):
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


def pick_top_repos(repos: List[dict], n: int = 8) -> List[dict]:
    candidates = [r for r in repos if _is_good_repo(r)]
    candidates.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
        reverse=True,
    )
    return candidates[:n]


def repo_lines_html(rs: List[dict], max_desc: int = 220) -> List[str]:
    """
    Single-line rows that DO NOT WRAP and ellipsize when too long.
    This is the most reliable styling for GitHub profile READMEs.
    """
    out: List[str] = []
    for r in rs:
        name = str(r.get("name", "")).strip()
        url = str(r.get("html_url", "")).strip()
        desc = _clean_desc(str(r.get("description") or ""), max_len=max_desc)

        suffix = f" — {desc}" if desc else ""

        # NOTE: no newlines in this HTML string (important)
        out.append(
            f'<div style="max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
            f'<a href="{url}"><b>{name}</b></a>{suffix}'
            f"</div>"
        )
    return out


def badges_html(username: str) -> List[str]:
    # HTML looks cleaner and prevents badge wrapping/scroll weirdness.
    return [
        '<p align="left">',
        f'  <a href="https://github.com/{username}?tab=followers"><img alt="Followers" src="https://img.shields.io/github/followers/{username}?label=Followers&style=flat"></a>',
        f'  <a href="https://github.com/{username}?tab=repositories"><img alt="Stars" src="https://img.shields.io/github/stars/{username}?label=Stars&style=flat"></a>',
        "</p>",
    ]


def section(title: str, lines: List[str]) -> List[str]:
    if not lines:
        return []
    return ["", f"## {title}", *lines]


def generate_readme(username: str, user: dict, repos: List[dict], pinned: List[dict]) -> str:
    display_name = user.get("name") or username
    bio = (user.get("bio") or "").strip()
    location = (user.get("location") or "").strip()
    blog = (user.get("blog") or "").strip()
    profile_url = user.get("html_url", f"https://github.com/{username}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Exclude the profile README repo from lists
    profile_repo_name = username.lower()
    filtered_repos = [
        r for r in repos
        if _is_good_repo(r) and str(r.get("name", "")).lower() != profile_repo_name
    ]

    # Current projects = pinned order if available; else curated list.
    current_projects = pinned if pinned else curated_then_top(filtered_repos, n=6)

    recent = pick_recent_repos(filtered_repos, n=6)
    top = pick_top_repos(filtered_repos, n=8)

    # Links
    links: List[str] = []
    if location:
        links.append(f"- 📍 {location}")
    if blog:
        blog_url = blog if blog.startswith("http") else f"https://{blog}"
        links.append(f"- 🌐 {blog_url}")
    links.append(f"- 💻 {profile_url}")

    # Hero image: use HTML so it scales full width nicely.
    # (If the file doesn't exist, GitHub just shows a broken image, so put the file in the repo.)
    hero_lines = [
        f'<p align="left"><img src="{HERO_IMAGE_PATH}" alt="Hero image" width="100%"></p>',
    ]

    out: List[str] = []
    out.append(f"<!-- Auto-generated on {now}. Edit README.md or regenerate via make_readme.py. -->")
    out.extend(hero_lines)
    out.append(f"# Hi, I'm {display_name} 👋")
    if bio:
        out.append("")
        out.append(bio)

    out.append("")
    out.extend(badges_html(username))

    out.append("")
    out.append("---")

    out += section("What I do", [f"- {x}" for x in WHAT_I_DO_BULLETS])
    out.append("")
    out.append("---")

    out += section("Current projects", repo_lines_html(current_projects, max_desc=170))
    out.append("")
    out.append("---")

    out += section("Recently updated", repo_lines_html(recent, max_desc=170))
    out.append("")
    out.append("---")

    out += section("More repos", repo_lines_html(top, max_desc=170))
    out.append("")
    out.append("---")

    out += section("Links", links)

    return "\n".join(out).rstrip() + "\n"


def main(argv: List[str]) -> int:
    username = argv[1] if len(argv) >= 2 else "AlanRockefeller"
    out_path = argv[2] if len(argv) >= 3 else "README.md"

    # Optional: token for requests-based GraphQL (not required if gh auth is set up)
    token = os.environ.get("GITHUB_TOKEN")

    user = fetch_user(username, token)
    repos = fetch_repos(username, token)
    pinned = fetch_pinned_repos(username, token)

    readme = generate_readme(username, user, repos, pinned)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"Wrote {out_path} for {username} ({len(repos)} repos, {len(pinned)} pinned)")
    if not pinned:
        print("Note: pinned repos unavailable. Ensure `gh auth login` works or set GITHUB_TOKEN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
