#!/usr/bin/env python3
"""Call a RedFoxHub API endpoint. Standard library only.

Credentials are read from --token, REDFOX_API_KEY, or a .env file next to this
script / in a parent directory. The key is never written into this folder.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_URL = "https://redfox.hk"
AUTH_HEADER = "REDFOX_API_KEY"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SUCCESS_CODE = 2000
# 3203 / 3801 cover both "no matching data" and genuine parameter errors.
# Only these message fragments (seen in live responses) mean an empty result,
# so "关键词长度请不要超过6个字" stays a real error.
NO_DATA_CODES = {3203, 3801}
NO_DATA_PHRASES = (
    "未查询到相关数据", "暂未收录", "不在库", "库内不存在",
    "未找到该账号", "数据不存在", "暂无作品", "账号不存在", "无相关数据",
    "资源在平台上未找到",  # 3203 "积分未扣除: API错误: 所请求的资源在平台上未找到。"
)
RETRYABLE = {429, 500, 502, 503, 504}


class TokenMissingError(Exception):
    pass


def read_env_token() -> Optional[str]:
    for key in ("REDFOX_API_KEY", "REDFOX_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def read_dotenv_token() -> Optional[str]:
    roots = [SCRIPT_DIR, *list(SCRIPT_DIR.parents)[:5], Path.cwd()]
    seen = set()
    for root in roots:
        f = root / ".env"
        try:
            key = f.resolve()
        except OSError:
            continue
        if key in seen or not f.is_file():
            continue
        seen.add(key)
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].strip()
                if line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() in ("REDFOX_API_KEY", "REDFOX_TOKEN"):
                    value = value.strip().strip('"').strip("'")
                    if value:
                        return value
        except (OSError, UnicodeDecodeError):
            continue
    return None


def restore_msys_value(value: str) -> str:
    """Git Bash rewrites '/story/api/...' args into 'D:/.../story/api/...'."""
    if not isinstance(value, str):
        return value
    m = re.search(r"[A-Za-z]:[\\/][^\\/]*(?:[\\/][^\\/]*)*?(/story/)", value)
    if m:
        return value[m.start(1):]
    m = re.search(r"[A-Za-z]:[\\/][^\\/]*(?:[\\/][^\\/]*)*?(/api/)", value)
    if m:
        return value[m.start(1):]
    return value


def parse_value(raw: str) -> Any:
    """Parse JSON-ish values in k=v so lists/dicts/numbers keep their type."""
    raw = restore_msys_value(raw)
    if raw and raw[0] in "[{":
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw)
        except ValueError:
            return raw
    if re.fullmatch(r"-?\d+\.\d+", raw):
        try:
            return float(raw)
        except ValueError:
            return raw
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw in ("null", "None"):
        return None
    return raw


def parse_kv(items) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for item in items:
        for pair in str(item).split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                params[key.strip()] = parse_value(value)
    return params


def normalize_path(path: str) -> str:
    path = restore_msys_value(path)
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    return path


class RedFoxClient:
    def __init__(
        self,
        api_token: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 0,
        require_token: bool = True,
    ):
        token = api_token or read_env_token() or read_dotenv_token()
        if require_token and not token:
            raise TokenMissingError(
                "No RedFox API key found.\n"
                "  set it:  export REDFOX_API_KEY=<your key>\n"
                "  or put REDFOX_API_KEY=<your key> in a .env OUTSIDE this folder\n"
                "  or pass: --token <your key>\n"
                "Get a key at https://redfox.hk/settings/api-keys"
            )
        self.api_token = token or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = os.environ.get("REDFOX_USER_AGENT", DEFAULT_UA)

    def _headers(self, multipart_boundary: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        if self.api_token:
            headers[AUTH_HEADER] = self.api_token
        if multipart_boundary:
            headers["Content-Type"] = "multipart/form-data; boundary=" + multipart_boundary
        else:
            headers["Content-Type"] = "application/json"
        return headers

    def _build_url(self, path: str, query: Dict[str, Any]) -> str:
        url = self.base_url + normalize_path(path)
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        return url

    @staticmethod
    def _decode(body: bytes):
        text = body.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text[:4000]}

    def _once(self, method: str, url: str, headers, payload: Optional[bytes]):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return self._decode(resp.read()), None
        except urllib.error.HTTPError as exc:
            detail = self._decode(exc.read())
            return None, {
                "error": "HTTP %d: %s" % (exc.code, exc.reason),
                "status_code": exc.code,
                "details": detail,
            }
        except urllib.error.URLError as exc:
            return None, {"error": "Connection error: %s" % exc.reason}
        except TimeoutError:
            return None, {"error": "Request timed out after %ss" % self.timeout}
        except OSError as exc:
            return None, {"error": "Network error: %s" % exc}

    def request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
        upload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = method.upper()
        query = query or {}
        boundary = None
        if upload:
            boundary = "----redfox" + uuid.uuid4().hex
            chunks = []
            for key, value in (upload.get("fields") or {}).items():
                chunks.append(("--%s\r\n" % boundary).encode())
                chunks.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode())
                chunks.append(("%s\r\n" % value).encode())
            fname = upload["filename"]
            ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            chunks.append(("--%s\r\n" % boundary).encode())
            chunks.append(
                ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                 % (upload["field"], fname)).encode()
            )
            chunks.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
            chunks.append(upload["content"])
            chunks.append(("\r\n--%s--\r\n" % boundary).encode())
            payload = b"".join(chunks)
            if method == "GET":
                method = "POST"
        elif method in ("POST", "PUT", "PATCH"):
            payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        else:
            payload = None

        url = self._build_url(path, query)
        headers = self._headers(boundary)
        for attempt in range(self.max_retries + 1):
            result, err = self._once(method, url, headers, payload)
            if err and err.get("status_code") in RETRYABLE and attempt < self.max_retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            return err or result
        return {"error": "Request failed"}

    def get(self, path, query=None):
        return self.request("GET", path, query=query)

    def post(self, path, body=None, query=None):
        return self.request("POST", path, query=query, body=body)


def summarize(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"value": result}
    out = {k: v for k, v in result.items() if not isinstance(v, (dict, list))}
    for key in ("data", "details"):
        v = result.get(key)
        if isinstance(v, dict):
            out[key] = {"keys": list(v.keys())[:15],
                        "scalars": {k: x for k, x in v.items()
                                    if not isinstance(x, (dict, list))}}
        elif isinstance(v, list):
            out[key] = {"type": "list", "length": len(v)}
    return out


def classify_result(result) -> str:
    """Return 'ok', 'no_data' or 'error'.

    A no-data business code is an empty result, not a broken endpoint; batch
    research must not count it as a failed call. The response itself is printed
    or written unchanged - only the exit code and the stderr line differ.
    """
    if not isinstance(result, dict):
        return "ok"
    if result.get("error"):
        return "error"
    code = result.get("code")
    if code is None or code == SUCCESS_CODE:
        return "ok"
    msg = str(result.get("msg") or result.get("message") or "")
    try:
        numeric = int(code)
    except (TypeError, ValueError):
        numeric = None
    if numeric in NO_DATA_CODES and any(p in msg for p in NO_DATA_PHRASES):
        return "no_data"
    return "error"


def business_failed(result) -> bool:
    """True only for real failures; no-data results are not failures."""
    return classify_result(result) == "error"


def charge_note(result) -> str:
    """Whether a no-data response cost points, taken from the live msg text.

    Exit code 0 only means "not a failure", not "free": 3203 usually says the
    points were not deducted, while 3801 has been seen to charge 0.600.
    """
    msg = str(result.get("msg") or result.get("message") or "") if isinstance(result, dict) else ""
    if re.search(r"积分未扣除|未消耗积分|未扣除|不扣费", msg):
        return "积分未扣除"
    paid = re.search(r"已扣除\s*([\d.]+\s*积分)?", msg)
    if paid:
        return "已扣除 " + (paid.group(1) or "积分")
    return "扣费情况见 msg"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Call a RedFoxHub API endpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  api_client.py GET /story/web/points/api-key/balance
  api_client.py POST /story/api/dyData/searchUser "keyword=乐高"
  api_client.py POST /story/api/xhsUser/searchArticle "keyword=积木" "offset=0" --summary
  api_client.py POST /story/api/hotSpot/getListByPlatformWithKeyword \\
      --body '{"keywords":["三星"],"startDate":"2026-11-01","endDate":"2026-11-14"}'
  api_client.py GET /story/api/hotSpot/getListByPlatform "platform=2" \\
      "startDate=2026-11-13" "endDate=2026-11-14" --output rank.json
""",
    )
    parser.add_argument("method", help="GET or POST")
    parser.add_argument("path", help="endpoint path, e.g. /story/api/dyData/searchUser")
    parser.add_argument("args", nargs="*", help="k=v params; a leading {...} is a JSON body")
    parser.add_argument("--body", help="JSON body for POST")
    parser.add_argument("--token", help="API key (overrides env/.env)")
    parser.add_argument("--base-url", help="override base URL")
    parser.add_argument("--file", help="local file to upload (multipart)")
    parser.add_argument("--field", default="file", help="multipart field name (default: file)")
    parser.add_argument("--no-auth", action="store_true", help="skip token requirement")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=0,
                        help="retries on 429/5xx (default 0, billed per call)")
    parser.add_argument("--output", help="write full JSON to a file instead of stdout")
    parser.add_argument("--compact", action="store_true", help="minified JSON output")
    parser.add_argument("--summary", action="store_true", help="print only shape + scalars")
    args = parser.parse_args()

    method = args.method.upper()
    if method not in ("GET", "POST"):
        print("ERROR: unsupported method %s (only GET / POST)" % method, file=sys.stderr)
        return 2

    body: Optional[Any] = None
    kv_items = []
    for item in args.args:
        if item.lstrip().startswith("{"):
            try:
                body = json.loads(item)
            except json.JSONDecodeError as exc:
                print("ERROR: invalid JSON body: %s" % exc, file=sys.stderr)
                return 2
        else:
            kv_items.append(item)
    if args.body:
        try:
            extra = json.loads(args.body)
            body = {**(body or {}), **extra} if isinstance(body, dict) else extra
        except json.JSONDecodeError as exc:
            print("ERROR: invalid --body JSON: %s" % exc, file=sys.stderr)
            return 2

    params = parse_kv(kv_items)
    try:
        client = RedFoxClient(
            api_token=args.token,
            base_url=args.base_url or BASE_URL,
            timeout=args.timeout,
            max_retries=args.retries,
            require_token=not args.no_auth,
        )
    except TokenMissingError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    upload = None
    if args.file:
        p = Path(args.file).expanduser()
        if not p.is_file():
            print("ERROR: file not found: %s" % p, file=sys.stderr)
            return 2
        upload = {"filename": p.name, "content": p.read_bytes(),
                  "field": args.field, "fields": params}
        params = {}

    if method == "POST":
        payload = body if body is not None else params
        result = client.request("POST", args.path, body=payload, upload=upload)
    else:
        if body is not None:
            print("ERROR: a JSON body was supplied for GET; use POST", file=sys.stderr)
            return 2
        result = client.request("GET", args.path, query=params, upload=upload)

    rendered = json.dumps(
        summarize(result) if args.summary else result,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.output:
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Wrote %s (%d bytes)" % (out.resolve(), out.stat().st_size))
    else:
        print(rendered)
        if len(rendered) > 30000 and not args.summary:
            print("\n[note] large response - consider --summary or --output result.json",
                  file=sys.stderr)

    kind = classify_result(result)
    if kind == "no_data":
        print("RedFox code %s (no matching data, not a failure; %s): %s"
              % (result.get("code"), charge_note(result), result.get("msg", "")),
              file=sys.stderr)
        return 0
    if kind == "error":
        if isinstance(result, dict) and result.get("error"):
            print("API error: %s" % result["error"], file=sys.stderr)
        else:
            print("RedFox code %s: %s" % (result.get("code"), result.get("msg", "")),
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
