from __future__ import annotations


def pr_url_from_number(owner: str, repo: str, number: int) -> str:
    return f"https://github.com/{owner}/{repo}/pull/{number}"
