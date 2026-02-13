#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

API = "https://api.github.com"
GQL = "https://api.github.com/graphql"

# Your preferred (featured) repos, in the order you want them shown.
# If a repo doesn't exist, it will be skipped and replaced by other top repos.
PREFERRED_REPOS = [
    "inat.label.py",
    "inat.finder.py",
    "faststack",
    "inat.nearbyobservations.py",
    "stackcopy",
    "motoinat.py",
]


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
    This avoids variables handling (which can be flaky across gh versions).
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
    # Pull up to 200 repos (2 pages). Increase if you need.
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
    Try GraphQL pinned repos via:
      1) requests + token (if set)
      2) gh api graphql (inline query) fallback (uses gh auth login)
    """
    # 1) Token-based GraphQL (variables version is fine here)
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
              primaryLanguage { name }
              updatedAt
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
        # If token is present but invalid, don't block the gh fallback.
        data = None

    # 2) gh CLI fallback (inline query; no variables)
    if not data:
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
                "updated_at": n.get("updatedAt") or "",
                "pushed_at": n.get("updatedAt") or "",
                "fork": False,
                "archived": False,
            }
        )
    return pinned


def _is_good_repo(r: dict) -> bool:
    return not r.get("fork") and not r.get("archived")


def pick_top_repos(repos: List[dict], n: int = 8) -> List[dict]:
    candidates = [r for r in repos if _is_good_repo(r)]
    candidates.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
        reverse=True,
    )
    return candidates[:n]


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


def most_common_languages(repos: List[dict]) -> List[str]:
    counts: Dict[str, int] = {}
    for r in repos:
        if not _is_good_repo(r):
            continue
        lang = (r.get("language") or "").strip()
        if not lang:
            continue
        counts[lang] = counts.get(lang, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]


def shields_badges(username: str, primary_langs: List[str]) -> str:
    # Put badges on separate lines to prevent horizontal scrolling.
    lines = [
        f"[![Followers](https://img.shields.io/github/followers/{username}?label=Followers&style=flat)](https://github.com/{username}?tab=followers)",
        f"[![Stars](https://img.shields.io/github/stars/{username}?label=Stars&style=flat)](https://github.com/{username}?tab=repositories)",
    ]
    # Keep language badges separate lines and limited count.
    for lang in primary_langs[:4]:
        safe = lang.replace(" ", "%20")
        lines.append(f"![{lang}](https://img.shields.io/badge/{safe}-informational?style=flat)")
    return "\n".join(lines)


def _clean_desc(desc: str, max_len: int = 90) -> str:
    d = " ".join(desc.strip().split())
    if len(d) <= max_len:
        return d
    return d[: max_len - 1].rstrip() + "…"


def format_repo_line(r: dict) -> str:
    # Compact bullets to avoid horizontal scrolling.
    name = r.get("name", "")
    url = r.get("html_url", "")
    desc = _clean_desc(r.get("description") or "")
    if desc:
        return f"- **[{name}]({url})** — {desc}"
    return f"- **[{name}]({url})**"


def generate_readme(username: str, user: dict, repos: List[dict], pinned: List[dict]) -> str:
    display_name = user.get("name") or username
    bio = (user.get("bio") or "").strip()
    location = (user.get("location") or "").strip()
    blog = (user.get("blog") or "").strip()
    profile_url = user.get("html_url", f"https://github.com/{username}")

    langs = most_common_languages(repos)
    badges = shields_badges(username, langs)

    # Featured logic:
    # - Always start with curated list
    # - Merge pinned if available (no duplicates)
    curated = curated_then_top(repos, n=6)
    featured: List[dict] = []
    seen = set()

    for r in curated + (pinned or []):
        key = str(r.get("name", "")).lower()
        if key and key not in seen and _is_good_repo(r):
            featured.append(r)
            seen.add(key)
        if len(featured) >= 6:
            break

    top = pick_top_repos(repos, n=8)
    recent = pick_recent_repos(repos, n=6)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    contact_bits: List[str] = []
    if location:
        contact_bits.append(f"- 📍 {location}")
    if blog:
        blog_url = blog if blog.startswith("http") else f"https://{blog}"
        contact_bits.append(f"- 🌐 {blog_url}")
    contact_bits.append(f"- 💻 {profile_url}")
    contact = "\n".join(contact_bits)

    featured_lines = "\n".join(format_repo_line(r) for r in featured)
    recent_lines = "\n".join(format_repo_line(r) for r in recent)
    top_lines = "\n".join(format_repo_line(r) for r in top)

    return textwrap.dedent(
        f"""\
        <!-- Auto-generated on {now}. Edit this file or regenerate via make_readme.py. -->

        # Hi, I'm {display_name} 👋

        {bio}

        {badges}

        ---

        ## Current projects
        {featured_lines}

        ---

        ## Recently updated
        {recent_lines}

        ---

        ## More repos
        {top_lines}

        ---

        ## Links
        {contact}
        """
    ).strip() + "\n"


def main(argv: List[str]) -> int:
    username = argv[1] if len(argv) >= 2 else "AlanRockefeller"
    out_path = argv[2] if len(argv) >= 3 else "README.md"

    # Optional: token for requests-based GraphQL. Not required if gh auth is set up.
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

