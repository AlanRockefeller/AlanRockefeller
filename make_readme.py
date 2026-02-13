#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import textwrap
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


API = "https://api.github.com"
GQL = "https://api.github.com/graphql"


def _headers(token: Optional[str]) -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-generator",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get_json(url: str, token: Optional[str], params: Optional[dict] = None) -> Any:
    r = requests.get(url, headers=_headers(token), params=params, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {url} failed: {r.status_code}\n{r.text[:500]}")
    return r.json()


def _post_gql(query: str, token: Optional[str], variables: dict) -> Any:
    if not token:
        # GraphQL is much more reliable with auth; keep behavior explicit.
        return None
    r = requests.post(
        GQL,
        headers=_headers(token),
        json={"query": query, "variables": variables},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"POST GraphQL failed: {r.status_code}\n{r.text[:500]}")
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


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
    # Pinned repos are only easily accessible via GraphQL.
    query = """
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
    data = _post_gql(query, token, {"login": username})
    if not data:
        return []
    nodes = data["user"]["pinnedItems"]["nodes"] or []
    # Normalize to plain dicts
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
            }
        )
    return pinned


def pick_top_repos(repos: List[dict], n: int = 8) -> List[dict]:
    # Filter out forks/archived unless they’re clearly popular.
    candidates = []
    for r in repos:
        if r.get("fork"):
            continue
        if r.get("archived"):
            continue
        candidates.append(r)
    # Sort by stars, then recency
    candidates.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
        reverse=True,
    )
    return candidates[:n]


def pick_recent_repos(repos: List[dict], n: int = 6) -> List[dict]:
    # Most recently pushed-to (already sorted by pushed in fetch, but keep safe)
    repos_sorted = sorted(repos, key=lambda r: r.get("pushed_at", ""), reverse=True)
    out = []
    for r in repos_sorted:
        if r.get("fork") or r.get("archived"):
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


def shields_badges(username: str, primary_langs: List[str]) -> str:
    # Keep it tasteful: a couple of “identity” badges + a few language badges.
    # You can change labels/colors later.
    parts = [
        f"[![Followers](https://img.shields.io/github/followers/{username}?label=Followers&style=flat)](https://github.com/{username}?tab=followers)",
        f"[![Stars](https://img.shields.io/github/stars/{username}?label=Total%20Stars&style=flat)](https://github.com/{username}?tab=repositories)",
    ]
    # Add up to 6 language badges
    for lang in primary_langs[:6]:
        safe = lang.replace(" ", "%20")
        parts.append(f"![{lang}](https://img.shields.io/badge/{safe}-informational?style=flat)")
    return " ".join(parts)


def format_repo_line(r: dict) -> str:
    name = r.get("name", "")
    url = r.get("html_url", "")
    desc = (r.get("description") or "").strip()
    stars = r.get("stargazers_count", 0)
    forks = r.get("forks_count", 0)
    lang = (r.get("language") or "").strip()
    meta = " • ".join([x for x in [lang, f"★ {stars}", f"⑂ {forks}"] if x])
    if desc:
        return f"- **[{name}]({url})** — {desc}  \n  {meta}"
    return f"- **[{name}]({url})**  \n  {meta}"


def most_common_languages(repos: List[dict]) -> List[str]:
    counts: Dict[str, int] = {}
    for r in repos:
        lang = (r.get("language") or "").strip()
        if not lang:
            continue
        counts[lang] = counts.get(lang, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]


def generate_readme(username: str, user: dict, repos: List[dict], pinned: List[dict]) -> str:
    display_name = user.get("name") or username
    bio = (user.get("bio") or "").strip()
    location = (user.get("location") or "").strip()
    blog = (user.get("blog") or "").strip()
    twitter = (user.get("twitter_username") or "").strip()
    avatar = user.get("avatar_url", "")
    profile_url = user.get("html_url", f"https://github.com/{username}")

    langs = most_common_languages(repos)
    badges = shields_badges(username, langs)

    top = pick_top_repos(repos, n=8)
    recent = pick_recent_repos(repos, n=6)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # If pinned is empty (no token / no pinned), fall back to top repos list.
    featured = pinned if pinned else top[:6]

    contact_bits = []
    if location:
        contact_bits.append(f"- 📍 {location}")
    if blog:
        # blog may be a bare domain; make it clickable
        blog_url = blog if blog.startswith("http") else f"https://{blog}"
        contact_bits.append(f"- 🌐 {blog_url}")
    if twitter:
        contact_bits.append(f"- 🐦 https://twitter.com/{twitter}")
    contact = "\n".join(contact_bits) if contact_bits else "- (add contact links here)"

    featured_lines = "\n".join(format_repo_line(r) for r in featured)
    top_lines = "\n".join(format_repo_line(r) for r in top)
    recent_lines = "\n".join(format_repo_line(r) for r in recent)

    # Keep it “steipete-ish”: big hello, badges, sections, curated lists.
    return textwrap.dedent(
        f"""\
        <!--
        Auto-generated on {now}.
        Edit generate_profile_readme.py to change layout, sections, or selection logic.
        -->

        # Hi, I'm {display_name} 👋

        {bio if bio else ""}

        {badges}

        ---

        ## Featured
        {featured_lines}

        ---

        ## Top repos
        {top_lines}

        ---

        ## Recently updated
        {recent_lines}

        ---

        ## About
        - 💻 {profile_url}
        - 🧩 I build practical tools for real workflows (automation, data, imaging, etc.)
        - 🧪 Interests: mycology, DNA barcoding, field photography, microscopy

        ## Contact
        {contact}

        ---
        <sub>Generated by <code>generate_profile_readme.py</code>. Avatar: {avatar}</sub>
        """
    ).strip() + "\n"


def main(argv: List[str]) -> int:
    username = argv[1] if len(argv) >= 2 else "AlanRockefeller"
    out_path = argv[2] if len(argv) >= 3 else "README.md"

    token = os.environ.get("GITHUB_TOKEN")

    user = fetch_user(username, token)
    repos = fetch_repos(username, token)
    pinned = fetch_pinned_repos(username, token)

    readme = generate_readme(username, user, repos, pinned)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(readme)

    print(f"Wrote {out_path} for {username} ({len(repos)} repos, {len(pinned)} pinned)")
    if not token:
        print("Note: set GITHUB_TOKEN to avoid rate limits and to fetch pinned repos via GraphQL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
