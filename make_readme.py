#!/usr/bin/env python3
"""Generate a GitHub profile README.md from live repo data.

Usage:
    python make_readme.py [USERNAME] [OUTPUT_PATH] [--dry-run]

Requires: requests
Optional: gh CLI (fallback for pinned repos when GITHUB_TOKEN is unset)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests

API = "https://api.github.com"
GQL = "https://api.github.com/graphql"

# -------------------- CONFIG --------------------

# Hero image path inside repo (relative). Recommended: 1280-1600px wide, ~400px tall.
HERO_IMAGE_PATH = "hero.jpg"
HERO_ALT = "Favolaschia calocera from New Zealand"

# Target total characters per project line before truncating the description.
# Curated blurbs are never truncated; only GitHub descriptions are.
TARGET_LINE_CHARS = 120

# How many items per category section (0 = unlimited)
MAX_PER_CATEGORY = 0

# ---- Featured projects ----
# Shown at the top of the README, before category sections.
# Pinned repos are automatically included first (in pinned order).
# Add repo names here to feature them alongside pinned repos, beyond
# GitHub's 6-pin limit.  These appear after pinned repos, in list order.
EXTRA_FEATURED: List[str] = [
    "Dikarya-phylogenetic-tree-generator",
    "Observation-Workbench",
]

# ---- Use it in your browser ----
# Shown above Featured projects.  {repo name: (title, blurb)}; the link is
# the repo's GitHub homepage field, so set that on GitHub, not here.
BROWSER_APPS: Dict[str, tuple] = {
    "Dikarya-phylogenetic-tree-generator": (
        "Dikarya",
        "Build phylogenetic trees from iNaturalist or Mushroom Observer "
        "observations, GenBank accessions or your own FASTA sequences",
    ),
    "inat.label.py": (
        "Herbarium label generator",
        "Make herbarium labels from iNaturalist or Mushroom Observer observations",
    ),
}

# ---- Intro ----
# {location} is filled in from the GitHub profile's location field.

INTRO = (
    "Mycologist based in {location}. I write open-source tools for DNA "
    "barcoding, phylogenetics, iNaturalist, Mushroom Observer and macro "
    "photography."
)

# ---- Profile links (displayed as named markdown links) ----

PROFILE_LINKS: Dict[str, str] = {
    "iNaturalist observations": "https://www.inaturalist.org/observations/alan_rockefeller",
    "Mushroom Observer": "https://mushroomobserver.org/observations?user=123",
    "Instagram": "https://www.instagram.com/alan_rockefeller",
}

# ---- Category-based repo grouping ----
# Repos are displayed under their category heading.  Ordering within a
# category follows the list order here; repos not listed are gathered
# into an "Other tools" bucket sorted by stars then recency, with a
# warning.  Map a repo to "Other tools" explicitly to silence it.

CATEGORY_ORDER = [
    "DNA & phylogenetics",
    "Photography & media",
    "Utilities",
    "iNaturalist tools",
]

REPO_CATEGORIES: Dict[str, str] = {
    # iNaturalist tools
    "inat.label.py":                "iNaturalist tools",
    "inat.finder.py":               "iNaturalist tools",
    "inat.nearbyobservations.py":   "iNaturalist tools",
    "inat.visualizer.py":           "iNaturalist tools",
    "inat.photodownloader.py":      "iNaturalist tools",
    "inat.orders.py":               "iNaturalist tools",
    "motoinat.py":                  "iNaturalist tools",
    "inat-gb-name.pl":              "iNaturalist tools",
    "Observation-Workbench":        "iNaturalist tools",
    # DNA & phylogenetics
    "Dikarya-phylogenetic-tree-generator": "DNA & phylogenetics",
    "fixfasta.py":                  "DNA & phylogenetics",
    "Treecraft":                    "DNA & phylogenetics",
    "convert.treebase.nexus.to.fasta.py": "DNA & phylogenetics",
    # Photography & media
    "faststack":                    "Photography & media",
    "stackcopy":                    "Photography & media",
    "findphotodates.py":            "Photography & media",
    "video-rename":                 "Photography & media",
    "photos_to_presentation":       "Photography & media",
    # Utilities
    "honeypath":                    "Utilities",
    "Lost-Phone-Finder":            "Utilities",
    "printfunction.sh":             "Utilities",
    "rmdup.py":                     "Utilities",
    "stock.crash.monitor.py":       "Utilities",
    # Other tools
    "taxonomybot.py":               "Other tools",
}

# Curated short blurbs (override GitHub descriptions).
# A website link is appended automatically from each repo's homepage field.
CURATED_BLURBS: Dict[str, str] = {
    "inat.label.py":                "Command line herbarium label generator for iNaturalist and Mushroom Observer observations",
    "Dikarya-phylogenetic-tree-generator": "Phylogenetic tree builder that runs IQ-TREE, RAxML-NG or MrBayes",
    "Observation-Workbench":        "Desktop app for identifying, editing and syncing iNaturalist and Mushroom Observer observations",
    "honeypath":                    "Plants fake credential files and alerts you when malware reads them (Linux, WSL, macOS)",
    "Lost-Phone-Finder":            "Android app that finds a phone lost in the backcountry by its Bluetooth signal",
    "convert.treebase.nexus.to.fasta.py": "Convert TreeBASE NEXUS downloads to FASTA",
    "photos_to_presentation":       "Make a LibreOffice Impress or PowerPoint slideshow from a folder of photos",
    "inat.visualizer.py":           "Graph the seasonal occurrence of any organism anywhere in the world from iNaturalist data",
    "inat.photodownloader.py":      "Make a CSV of the original filenames of your iNaturalist photos, and optionally download them",
    "inat.orders.py":               "Summarize iNaturalist observations by order, and optionally by family",
    "taxonomybot.py":               "PyWikiBot that adds References and Gallery sections to species pages on mycomap.org",
    "inat.finder.py":               "Find the right iNaturalist observation when you have a mistyped number",
    "faststack":                    "Fast photo viewer with basic editing and uploading",
    "inat.nearbyobservations.py":   "Browser extension that finds nearby iNaturalist observations in the same genus",
    "stackcopy":                    "Photo import tool for Olympus cameras that handles in-camera focus stacking",
    "motoinat.py":                  "Find the iNaturalist observation that matches a Mushroom Observer observation number",
    "findphotodates.py":            "List your photos and videos with the dates they were taken, using exiftool",
    "printfunction.sh":             "Print Python function definitions from the command line using AST parsing",
    "Treecraft":                    "GUI program for building and editing phylogenetic trees, written in Python with PyQt6",
    "video-rename":                 "Rename video files based on where they were shot and their orientation",
}

# Prefixes to strip from GitHub descriptions that weren't curated.
_BOILERPLATE_PREFIXES = [
    "a python script which ",
    "a python script that ",
    "a python program which ",
    "a python program that ",
    "a perl script which ",
    "a perl script that ",
    "a bash script which ",
    "a bash script that ",
    "a gui ",
]

# -------------------- END CONFIG --------------------


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _headers(token: Optional[str]) -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-generator",
    }
    if token:
        t = token.strip()
        if any(c.isspace() for c in t):
            _log("Warning: GITHUB_TOKEN contains whitespace — ignoring it.")
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
    """Run a GraphQL query via `gh api graphql` using existing gh auth."""
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        _log("Warning: `gh` CLI not found — skipping pinned repos fallback.")
        return None
    if proc.returncode != 0:
        _log(f"Warning: gh graphql failed (rc={proc.returncode}): {proc.stderr[:200]}")
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        _log(f"Warning: gh graphql returned invalid JSON: {proc.stdout[:200]}")
        return None


# -------------------- Data fetching --------------------


def fetch_user(username: str, token: Optional[str]) -> dict:
    return _get_json(f"{API}/users/{username}", token)


def fetch_repos(username: str, token: Optional[str]) -> List[dict]:
    repos: List[dict] = []
    for page in (1, 2):
        try:
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
        except RuntimeError as e:
            _log(f"Warning: failed to fetch repos page {page}: {e}")
            break
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
              homepageUrl
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
    except Exception as e:
        _log(f"Warning: GraphQL pinned query failed: {e}")

    if not data:
        query_inline = (
            '{ user(login:"' + username + '") {'
            "  pinnedItems(first:6, types:REPOSITORY) {"
            "    nodes { ... on Repository {"
            "      name url description homepageUrl stargazerCount forkCount"
            "      updatedAt primaryLanguage { name }"
            "    } }"
            "  }"
            "} }"
        )
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
                "homepage": n.get("homepageUrl") or "",
                "stargazers_count": n.get("stargazerCount") or 0,
                "forks_count": n.get("forkCount") or 0,
                "language": (n.get("primaryLanguage") or {}).get("name") or "",
                "pushed_at": n.get("updatedAt") or "",
                "archived": False,
                "fork": False,
            }
        )
    return pinned


# -------------------- Repo classification --------------------


def _is_good_repo(r: dict) -> bool:
    return not r.get("fork") and not r.get("archived")


def _repo_key(r: dict) -> str:
    return str(r.get("name", "")).strip()


def _strip_boilerplate(desc: str) -> str:
    """Remove generic 'A python script which ...' prefixes and capitalise."""
    dl = desc.lower()
    for prefix in _BOILERPLATE_PREFIXES:
        if dl.startswith(prefix):
            rest = desc[len(prefix):]
            return rest[0].upper() + rest[1:] if rest else desc
    return desc


def _clean_desc(desc: str) -> str:
    d = " ".join(str(desc).split())
    return _strip_boilerplate(d)


def _truncate(text: str, max_len: int) -> str:
    """Cut at a word boundary so the result (with ellipsis) fits max_len."""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-–—") + "…"


def _homepage(repo: dict) -> str:
    """The repo's GitHub homepage field as an absolute URL, or ''."""
    url = str(repo.get("homepage") or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url


def _sort_key_stars_recency(r: dict) -> tuple:
    return (r.get("stargazers_count", 0), r.get("pushed_at", ""))


def _build_repo_index(
    repos: List[dict],
    pinned: List[dict],
) -> Dict[str, dict]:
    """Index all usable repos by name (pinned included as fallback)."""
    by_name: Dict[str, dict] = {}
    for r in repos:
        if _is_good_repo(r):
            by_name[_repo_key(r)] = r
    for r in pinned:
        key = _repo_key(r)
        if key and key not in by_name and _is_good_repo(r):
            by_name[key] = r
    return by_name


def build_featured(
    by_name: Dict[str, dict],
    pinned: List[dict],
) -> List[dict]:
    """
    Build the featured projects list: pinned repos first (in pinned order),
    then EXTRA_FEATURED repos (in list order), deduped.
    """
    featured: List[dict] = []
    seen: Set[str] = set()

    # Pinned repos come first
    for r in pinned:
        key = _repo_key(r)
        if key and key not in seen and _is_good_repo(r):
            # Prefer the REST version if available (has more fields)
            featured.append(by_name.get(key, r))
            seen.add(key)

    # Then extras, in config order
    for name in EXTRA_FEATURED:
        if name not in seen:
            r = by_name.get(name)
            if r:
                featured.append(r)
                seen.add(name)

    return featured


def _build_category_map(
    by_name: Dict[str, dict],
    exclude: Set[str],
) -> Dict[str, List[dict]]:
    """
    Assign repos to categories, skipping any in `exclude` (featured repos).
    Returns {category_name: [repo, ...]}.
    """
    categorised: Dict[str, List[dict]] = {cat: [] for cat in CATEGORY_ORDER}
    categorised["Other tools"] = []

    used: Set[str] = set(exclude)

    # First pass: repos with explicit category assignments (preserves dict order)
    for repo_name, category in REPO_CATEGORIES.items():
        if repo_name in used:
            continue
        r = by_name.get(repo_name)
        if r:
            target = category if category in categorised else "Other tools"
            categorised[target].append(r)
            used.add(repo_name)

    # Second pass: remaining repos go to "Other tools", sorted by stars+recency
    remaining = [
        r for name, r in by_name.items()
        if name not in used
    ]
    remaining.sort(key=_sort_key_stars_recency, reverse=True)
    for r in remaining:
        _log(f"Warning: {_repo_key(r)} is uncategorized — add it to REPO_CATEGORIES.")
    categorised["Other tools"].extend(remaining)

    # Drop empty categories
    return {cat: items for cat, items in categorised.items() if items}


# -------------------- Markdown rendering --------------------


def _project_line(repo: dict) -> str:
    """Render one repo as a markdown list item: - **[name](url)** - blurb ([website](homepage))"""
    name = _repo_key(repo)
    url = str(repo.get("html_url", "")).strip()
    homepage = _homepage(repo)
    link = f" ([website]({homepage}))" if homepage else ""

    blurb = " ".join(CURATED_BLURBS.get(name, "").split())
    if not blurb:
        blurb = _clean_desc(repo.get("description") or "")
        reserve = len(name) + 10 + (len(" (website)") if homepage else 0)
        max_desc = max(60, TARGET_LINE_CHARS - reserve)
        if len(blurb) > max_desc:
            _log(f"Warning: description of {name} truncated — add it to CURATED_BLURBS.")
            blurb = _truncate(blurb, max_desc)

    if blurb:
        return f"- **[{name}]({url})** - {blurb}{link}"
    return f"- **[{name}]({url})**{link}"


def _browser_app_lines(by_name: Dict[str, dict]) -> List[str]:
    """Render BROWSER_APPS, linking each to its repo's homepage."""
    lines: List[str] = []
    for name, (title, blurb) in BROWSER_APPS.items():
        r = by_name.get(name)
        homepage = _homepage(r) if r else ""
        if not homepage:
            _log(f"Warning: {name} has no homepage on GitHub — left out of browser section.")
            continue
        lines.append(f"- **[{title}]({homepage})** - {blurb}")
    return lines


def _section(title: str, lines: List[str]) -> List[str]:
    if not lines:
        return []
    return ["", f"## {title}", ""] + lines


def generate_readme(
    username: str,
    user: dict,
    repos: List[dict],
    pinned: List[dict],
) -> str:
    location = (user.get("location") or "").strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Exclude the profile repo itself
    filtered = [
        r for r in repos
        if _is_good_repo(r) and _repo_key(r).lower() != username.lower()
    ]

    by_name = _build_repo_index(filtered, pinned)
    featured = build_featured(by_name, pinned)
    featured_names = {_repo_key(r) for r in featured}
    categories = _build_category_map(by_name, exclude=featured_names)

    # ---- Assemble markdown ----
    lines: List[str] = []

    lines.append(
        f"<!-- Auto-generated on {now}. Edit or regenerate via make_readme.py -->"
    )
    lines.append("")
    lines.append(f"![{HERO_ALT}]({HERO_IMAGE_PATH})")
    lines.append("")

    # Intro: what the code is for (the GitHub sidebar already shows the bio)
    if INTRO:
        lines.append(INTRO.format(location=location))
        lines.append("")

    lines.append("---")

    lines += _section("Use it in your browser", _browser_app_lines(by_name))

    # Featured projects (pinned + extras)
    if featured:
        lines += _section(
            "Featured projects",
            [_project_line(r) for r in featured],
        )

    # Category sections
    for category, cat_repos in categories.items():
        items = cat_repos
        if MAX_PER_CATEGORY > 0:
            items = items[:MAX_PER_CATEGORY]
        lines += _section(category, [_project_line(r) for r in items])

    lines.append("")
    lines.append("---")

    # Links
    link_lines = [f"- [{label}]({url})" for label, url in PROFILE_LINKS.items()]
    lines += _section("Links", link_lines)

    return "\n".join(lines).rstrip() + "\n"


# -------------------- CLI --------------------


def main(argv: List[str]) -> int:
    dry_run = "--dry-run" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]

    username = args[0] if len(args) >= 1 else "AlanRockefeller"
    out_path = args[1] if len(args) >= 2 else "README.md"

    token = os.environ.get("GITHUB_TOKEN")

    _log(f"Fetching data for {username}…")
    user = fetch_user(username, token)
    repos = fetch_repos(username, token)
    pinned = fetch_pinned_repos(username, token)

    readme = generate_readme(username, user, repos, pinned)

    if dry_run:
        print(readme)
        _log(f"(dry run — not writing to {out_path})")
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(readme)
        _log(f"Wrote {out_path} for {username} ({len(repos)} repos, {len(pinned)} pinned)")

    if not pinned:
        _log(
            "Note: pinned repos unavailable. "
            "Ensure `gh auth login` works or set GITHUB_TOKEN."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
