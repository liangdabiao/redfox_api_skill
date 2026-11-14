#!/usr/bin/env python3
"""Search / inspect RedFoxHub endpoints in apis.json (no network, no credentials)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def find_spec(explicit: Optional[str] = None) -> Path:
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("REDFOX_APIS_PATH")
    if env:
        candidates.append(Path(env).expanduser())
    for root in [SCRIPT_DIR, *list(SCRIPT_DIR.parents)[:5]]:
        candidates.append(root / "apis.json")
    seen = set()
    for c in candidates:
        try:
            key = c.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if c.is_file():
            return c
    raise FileNotFoundError(
        "apis.json not found. Set REDFOX_APIS_PATH or pass --spec.\n"
        "Checked: this skill folder and its parent directories."
    )


def tokenize(query: str) -> List[tuple]:
    """Split into (token, weight); Chinese runs also produce lower-weight bigrams."""
    tokens: List[tuple] = []
    for raw in TOKEN_RE.findall(query.lower().strip()):
        tokens.append((raw, 1.0))
        if CJK_RE.search(raw) and len(raw) > 2:
            tokens.extend((raw[i:i + 2], 0.4) for i in range(len(raw) - 1))
    best: Dict[str, float] = {}
    for token, weight in tokens:
        best[token] = max(best.get(token, 0.0), weight)
    return list(best.items())


def defaults_present(e: Dict[str, Any]) -> bool:
    return any(
        (not p["required"]) and p["default"] is not None
        and str(p["default"]) != "None" and p["api_name"]
        for p in e.get("params", [])
    )


class RedFoxAPISearcher:
    def __init__(self, spec_path: Optional[str] = None):
        self.spec_path = find_spec(spec_path)
        with open(self.spec_path, "r", encoding="utf-8") as fh:
            self.data = json.load(fh)
        self.endpoints: List[Dict[str, Any]] = self.data["endpoints"]
        self.last_total = 0

    # ------------------------------------------------------------------ list
    def platforms(self) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        labels: Dict[str, str] = {}
        for e in self.endpoints:
            counts[e["platform"]] = counts.get(e["platform"], 0) + 1
            labels[e["platform"]] = e["platform_label"]
        return [{"name": n, "label": labels[n], "count": c}
                for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def by_platform(self, platform: str) -> List[Dict[str, Any]]:
        p = platform.lower().strip()
        out = [e for e in self.endpoints if e["platform"] == p or p in e["platform_label"].lower()]
        return sorted(out, key=lambda e: e["sdk_method"])

    # ---------------------------------------------------------------- search
    @staticmethod
    def _fields(e: Dict[str, Any]) -> Dict[str, str]:
        param_names = " ".join(
            " ".join(str(x) for x in (p.get("sdk_name"), p.get("api_name"),
                                      p.get("description")) if x)
            for p in e.get("params", [])
        )
        return {
            "name": e["name"].lower(),
            "path": e["path"].lower(),
            "summary": e["summary"].lower(),
            "platform": (e["platform"] + " " + e["platform_label"]).lower(),
            "description": (e.get("description") or "").lower()[:1200],
            "params": param_names.lower(),
        }

    def search(self, keyword: str, limit: int = 15) -> List[Dict[str, Any]]:
        tokens = tokenize(keyword)
        if not tokens:
            return []
        phrase = keyword.lower().strip()
        weights = {"name": 3.0, "path": 3.0, "summary": 4.0,
                   "platform": 2.5, "description": 1.0, "params": 2.0}
        total_weight = sum(w for _, w in tokens) or 1.0
        primary = [t for t, w in tokens if w == 1.0]
        scored = []
        for e in self.endpoints:
            f = self._fields(e)
            hit = [(t, w) for t, w in tokens if any(t in v for v in f.values())]
            if not hit or sum(w for _, w in hit) / total_weight <= 0.6:
                continue
            score = sum(w * weights[k] for t, w in hit for k, v in f.items() if t in v)
            if score <= 0:
                continue
            if primary and all(any(t in v for v in f.values()) for t in primary):
                score += 6.0
            if phrase and phrase in f["summary"]:
                score += 8.0
            if phrase and phrase in f["path"]:
                score += 8.0
            # A bare platform name must surface that platform's own endpoints.
            # Measured on "account": unrelated get_account endpoints score 25.0
            # while account.balance scores 11.5, so the bonus needs > 13.5.
            if len(primary) == 1 and primary[0] == e["platform"]:
                score += 15.0
            if e["sdk_method"].startswith(("search", "get_", "query")):
                score += 2.0
            scored.append((score, len(e["path"]), e["name"], e))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))
        self.last_total = len(scored)
        return [x[3] for x in scored[:limit]]

    # ---------------------------------------------------------------- detail
    def detail(self, key: str, full: bool = False) -> Optional[Dict[str, Any]]:
        e = None
        for cand in self.endpoints:
            if key in (cand["name"], cand["path"], cand["sdk_method"]):
                e = cand
                break
        if e is None and key.startswith("/"):
            hits = [x for x in self.endpoints if key in x["path"]]
            if len(hits) == 1:
                e = hits[0]
        if e is None:
            low = key.lower()
            hits = [x for x in self.endpoints if low in x["name"].lower() or low in x["path"].lower()]
            if len(hits) == 1:
                e = hits[0]
        if e is None:
            return None
        out = {k: e[k] for k in (
            "name", "platform", "platform_label", "http_method", "path",
            "summary", "description", "returns", "example_call",
            "body_fields", "query_fields", "internal_fields",
        )}
        out["params"] = [
            {"api_name": p["api_name"], "sdk_name": p["sdk_name"],
             "required": p["required"], "type": p["type"],
             "default": p["default"], "description": p["description"]}
            for p in e["params"]
        ]
        out["tips"] = []
        if defaults_present(e):
            out["tips"].append(
                "部分标为可选的参数服务端实际必填；请照抄 example_call 里给出的默认值"
            )
        if e.get("is_upload"):
            out["tips"].append("这是 multipart 上传接口：用 --file <本地文件> --field file，其他参数照常写 k=v")
        if full:
            out["raw"] = e
        return out


def _print_eps(items, title: str, total: Optional[int] = None) -> None:
    items = list(items)
    suffix = " (showing %d of %d matches)" % (len(items), total) if total else " (%d)" % len(items)
    print("\n=== %s%s ===\n" % (title, suffix))
    for i, e in enumerate(items, 1):
        print("%3d. %-5s %s" % (i, e["http_method"], e["path"]))
        print("     [%s] %s" % (e["name"], e["summary"][:100]))
        print("     op: %s" % e["example_call"][:150])
        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search the RedFoxHub endpoint catalog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  api_searcher.py "小红书 搜索 笔记"
  api_searcher.py "douyin hot rank" --limit 5
  api_searcher.py platforms
  api_searcher.py platform:xiaohongshu
  api_searcher.py detail:douyin.search_users
  api_searcher.py detail:/story/api/dyData/searchUser --json
""",
    )
    parser.add_argument("query", nargs="?", help="keywords | platforms | platform:X | detail:X")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--spec", help="path to apis.json")
    args = parser.parse_args()
    if not args.query:
        parser.print_help()
        return 1
    try:
        s = RedFoxAPISearcher(args.spec)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    q = args.query.strip()
    if q in ("platforms", "tags"):
        data = s.platforms()
        if args.as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print("\n=== %d platforms / %d endpoints ===\n" % (len(data), len(s.endpoints)))
            for x in data:
                print("  %4d  %-18s %s" % (x["count"], x["name"], x["label"]))
        return 0
    if q.startswith(("platform:", "tag:")):
        name = q.split(":", 1)[1].strip()
        items = s.by_platform(name)
        if not items:
            print("No endpoints for platform '%s'. Run `api_searcher.py platforms`." % name,
                  file=sys.stderr)
            return 1
        if args.as_json:
            print(json.dumps([{k: v for k, v in e.items()} for e in items],
                            ensure_ascii=False, indent=2))
        else:
            _print_eps(items, "Platform: %s" % name)
        return 0
    if q.startswith(("detail:", "path:")):
        d = s.detail(q.split(":", 1)[1].strip(), full=args.full)
        if not d:
            print("No endpoint found. Try `platforms` or a keyword search first.",
                  file=sys.stderr)
            return 1
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    if q.startswith("search:"):
        q = q[7:].strip()
    found = s.search(q, limit=args.limit)
    total = s.last_total
    if args.as_json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
    else:
        _print_eps(found, "Search: %r" % q, total=total)
        if not found:
            if total and args.limit == 0:
                print("%d matches found, but --limit 0 hides them all." % total)
            else:
                print("No match. Try fewer words, a platform name, or `platforms`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
