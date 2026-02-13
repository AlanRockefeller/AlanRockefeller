#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

API = "https://api.github.com"
GQL = "https://api.github.com/graphql"

# -------------------- CONFIG YOU MAY EDIT --------------------

# Hero image path inside repo (relative). e.g. "hero.jpg" or "assets/hero.png"
HERO_IMAGE_PATH = "hero.jpg"

# Target total characters per project line before we start truncating the description.
# This is the knob that reduces wrapping. Lower = less wrap, more ellipsis.
TARGET_LINE_CHARS = 120

# How many items to show in each section
N_CURRENT = 6
N_RECENT = 6
N_MORE = 8

# Repo names you consider “current projects” (preferred ordering). If pinned is available,
# pinned order wins; otherwise this ordering is used.
PREFERRED_REPOS = [
    "inat.label.py",
    "inat.finder.py",
    "faststack",
    "inat.nearbyobservations.py",
    "stackcopy",
    "motoinat.py",
]

# Curated short blurbs (these override GitHub descriptions)
# Format: repo_name -> (emoji, blurb)
CURATED_BLURBS: Dict[str, Tuple[str, str]] = {
    "inat.label.py": ("🏷️", "iNaturalist → herbarium label generator (RTF output)"),
    "inat.finder.py": ("🔎", "Fix mistyped iNaturalist observation IDs via permutation search"),
    "faststack": ("📷", "Fast photo viewer + lightweight editing + upload workflow"),
    "inat.nearbyobservations.py": ("🧭", "Find nearby same-genus iNaturalist observations (CLI + extension)"),
    "stackcopy": ("📸", "Olympus import tool that understands in-camera focus stacking"),
    "motoinat.py": ("🧬", "Map Mushroom Observer observation IDs → iNaturalist IDs"),
    "findphotodates.py": ("🗓️", "Inventory photos/videos by capture date (exiftool-backed)"),
    "printfunction.sh": ("🧰", "Print Python function definitions via AST (fast context for reviews)"),
}

WHAT_I_DO_BULLETS = [
    "🧬 DNA barcoding workflows (field → lab → sequences → IDs)",
    "📷 Field photography + automation pipelines for large datasets",
    "🔬 Fungal microscopy + documentation tooling",
]

# A short “vibe” quote like Peter’s blockquote
VIBE_QUOTE = (
    "Building practical tools for real workflows—mycology, imaging, and automation. "
    "I like fast CLIs, reproducible pipelines, and software that saves time in the field and lab."
)

# Badges (flat-square, logo) — keep it small like Peter’s
BADGES = [
    ("Python", "3776AB", "python", "white"),
    ("Shell", "4EAA25", "gnu-bash", "white"),
    ("Perl", "39457E", "perl", "white"),
    ("CLI", "000000", "terminal", "white"),
    ("Linux", "FCC624", "linux", "black"),
]

# -------------------- END CONFIG --------------------


def _headers(token: Optional[str]) -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-generator",
    }
    if token:
        t = token.strip()
        # Refuse tokens containing whitespace/newlines (common: user pasted CLI error output)
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
    if not token:
        return None
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    r = requests.post(GQL, headers=_headers(token), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"POST GraphQL failed: {r.status_code}\n{r.text[:500]}")
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data")


def _gh_graphql_inline(query: str) -> Optional[dict]:
    """
    Inline GraphQL via `gh api graphql` using the existing gh auth session.
    Works even when `gh auth token` is missing on older gh versions.
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
      1) GraphQL + token (if provided)
      2) `gh api graphql` inline query fallback (uses gh auth)
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
                "archived": False,
                "fork": False,
            }
        )
    return pinned


def _is_good_repo(r: dict) -> bool:
    return not r.get("fork") and not r.get("archived")


def _clean_desc(desc: str, max_len: int) -> str:
    d = " ".join(str(desc).replace("\n", " ").replace("\r", " ").strip().split())
    if len(d) <= max_len:
        return d
    return d[: max_len - 1].rstrip() + "…"


def pick_recent_repos(repos: List[dict], n: int) -> List[dict]:
    repos_sorted = sorted(repos, key=lambda r: r.get("pushed_at", ""), reverse=True)
    out: List[dict] = []
    for r in repos_sorted:
        if not _is_good_repo(r):
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out


def pick_top_repos(repos: List[dict], n: int) -> List[dict]:
    candidates = [r for r in repos if _is_good_repo(r)]
    candidates.sort(
        key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at", "")),
        reverse=True,
    )
    return candidates[:n]


def curated_then_top(repos: List[dict], n: int) -> List[dict]:
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


def badge_line() -> str:
    parts: List[str] = []
    for label, color, logo, logo_color in BADGES:
        # Example:
        # ![Python](https://img.shields.io/badge/-Python-3776AB?style=flat-square&logo=python&logoColor=white)
        parts.append(
            f"![{label}](https://img.shields.io/badge/-{label}-{color}?style=flat-square&logo={logo}&logoColor={logo_color})"
        )
    return "\n".join(parts)


def project_line(repo: dict) -> str:
    """
    - 🧭 **[repo](url)** — short blurb
    We dynamically truncate to reduce wrapping, since CSS no-wrap can't be relied on in GitHub READMEs.
    """
    name = str(repo.get("name", "")).strip()
    url = str(repo.get("html_url", "")).strip()

    emoji, blurb = CURATED_BLURBS.get(name, ("🔧", ""))

    if not blurb:
        # Fall back to GitHub description, but keep it short
        blurb = str(repo.get("description") or "").strip()

    # Dynamic truncation: aim for roughly TARGET_LINE_CHARS overall
    # Reserve a bit for markup and punctuation.
    reserve = len(name) + 12  # emoji + markdown + separators overhead
    max_desc = max(60, TARGET_LINE_CHARS - reserve)
    blurb = _clean_desc(blurb, max_desc)

    return f"- {emoji} **[{name}]({url})** — {blurb}"


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

    # Exclude the profile repo itself from “recent/top”
    profile_repo_name = username.lower()
    filtered_repos = [
        r for r in repos
        if _is_good_repo(r) and str(r.get("name", "")).lower() != profile_repo_name
    ]

    current = pinned if pinned else curated_then_top(filtered_repos, n=N_CURRENT)
    recent = pick_recent_repos(filtered_repos, n=N_RECENT)
    more = pick_top_repos(filtered_repos, n=N_MORE)

    # tagline (compact identity)
    tagline_bits: List[str] = []
    if location:
        tagline_bits.append(f"📍 **{location}**")
    tagline_bits.append("🍄 **Mycology + DNA barcoding**")
    tagline_bits.append("📷 **Field photography**")
    tagline_bits.append("🔬 **Fungal microscopy**")
    tagline = " | ".join(tagline_bits)

    # Links
    links: List[str] = []
    if blog:
        blog_url = blog if blog.startswith("http") else f"https://{blog}"
        links.append(f"- 🌐 {blog_url}")
    links.append(f"- 💻 {profile_url}")

    lines: List[str] = []
    lines.append(f"<!-- Auto-generated on {now}. Edit README.md or regenerate via make_readme.py. -->")
    lines.append(f'<p align="left"><img src="{HERO_IMAGE_PATH}" alt="Hero image" width="100%"></p>')
    lines.append(f"# Hi, I'm {display_name} 👋")
    lines.append("")
    lines.append(tagline)
    lines.append("")
    if bio:
        # keep bio short-ish; it's already one sentence on your profile
        lines.append(bio)
        lines.append("")
    lines.append(badge_line())
    lines.append("")
    lines.append(f"> {VIBE_QUOTE}")
    lines.append("")
    lines.append("---")

    lines += section("What I do", [f"- {x}" for x in WHAT_I_DO_BULLETS])
    lines.append("")
    lines.append("---")

    lines += section("Current projects", [project_line(r) for r in current])

    # Put the auto sections into details to keep the page clean like Peter’s
    lines.append("")
    lines.append("<details>")
    lines.append("<summary><b>Recently updated</b></summary>")
    lines.append("")
    lines.extend([project_line(r) for r in recent])
    lines.append("")
    lines.append("</details>")

    lines.append("")
    lines.append("<details>")
    lines.append("<summary><b>More repos</b></summary>")
    lines.append("")
    lines.extend([project_line(r) for r in more])
    lines.append("")
    lines.append("</details>")

    lines.append("")
    lines.append("---")
    lines += section("Links", links)

    return "\n".join(lines).rstrip() + "\n"


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
    if not pinned:
        print("Note: pinned repos unavailable. Ensure `gh auth login` works (for gh graphql fallback) or set GITHUB_TOKEN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
