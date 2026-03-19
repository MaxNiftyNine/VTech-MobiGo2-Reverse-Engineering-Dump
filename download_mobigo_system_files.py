#!/usr/bin/env python3
"""Download MobiGo console system files from VTech's live service.

This matches the request shape used by MobiGo.dll for
VTechDA.WService/getConsoleSystemFile on /wservices/consoles.asmx.

Auth note:
    The service requires a valid strToken. The app normally gets that from the
    Download Manager/web login flow. Pass it with --token, --token-file, or the
    VTECH_TOKEN environment variable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import requests
import urllib3


SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
SERVICE_NS = "VTechDA.WService"
SOAP_ENDPOINT = "https://www.vtechda.com/wservices/consoles.asmx"
SOAP_ACTION = "VTechDA.WService/getConsoleSystemFile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download MobiGo system files using the original VTech web service."
    )
    parser.add_argument("--pid", required=True, help="Console PID, e.g. 1158 or 11584")
    parser.add_argument("--country", required=True, help="Console country, e.g. US")
    parser.add_argument("--lang", required=True, help="Console language, e.g. eng")
    parser.add_argument("--token", help="VTech strToken value")
    parser.add_argument(
        "--token-file",
        help="Read the token from a file. The first token-looking value is used.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory where the downloaded files and manifest will be written",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds for SOAP and file downloads",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Query and save the manifest, but do not download binaries",
    )
    parser.add_argument(
        "--allow-bad-md5",
        action="store_true",
        help="Keep files even if the downloaded MD5 does not match the service manifest",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification",
    )
    return parser.parse_args()


def resolve_token(args: argparse.Namespace) -> str:
    for candidate in (args.token, os.environ.get("VTECH_TOKEN")):
        if candidate:
            return candidate.strip()

    if args.token_file:
        text = Path(args.token_file).read_text(encoding="utf-8", errors="ignore")
        # Prefer an XML/JSON-ish token value if present, else fall back to the first
        # long non-whitespace field.
        patterns = (
            r"<token>([^<]+)</token>",
            r'"token"\s*:\s*"([^"]+)"',
            r"\b([A-Za-z0-9._~+/=-]{20,})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()

    raise SystemExit(
        "No token supplied. Use --token, --token-file, or set VTECH_TOKEN."
    )


def build_get_console_system_file_envelope(token: str, pid: str, country: str, lang: str) -> str:
    return textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                       xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                       xmlns:soap="{SOAP_NS}">
          <soap:Header>
            <CommonHeader xmlns="{SERVICE_NS}">
              <strToken>{xml_escape(token)}</strToken>
            </CommonHeader>
          </soap:Header>
          <soap:Body>
            <getConsoleSystemFile xmlns="{SERVICE_NS}">
              <PID>{xml_escape(pid)}</PID>
              <sCountry>{xml_escape(country)}</sCountry>
              <sLang>{xml_escape(lang)}</sLang>
            </getConsoleSystemFile>
          </soap:Body>
        </soap:Envelope>
        """
    )


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def request_manifest(
    session: requests.Session,
    token: str,
    pid: str,
    country: str,
    lang: str,
    timeout: int,
    verify: bool,
) -> dict:
    body = build_get_console_system_file_envelope(token, pid, country, lang)
    response = session.post(
        SOAP_ENDPOINT,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": SOAP_ACTION,
        },
        timeout=timeout,
        verify=verify,
    )
    response.raise_for_status()
    return parse_get_console_system_file_response(response.text)


def parse_get_console_system_file_response(xml_text: str) -> dict:
    ns = {"soap": SOAP_NS, "svc": SERVICE_NS}
    root = ET.fromstring(xml_text)
    result = root.find(".//svc:getConsoleSystemFileResult", ns)
    if result is None:
        raise RuntimeError("SOAP response did not contain getConsoleSystemFileResult")

    generic = parse_generic_msg(result.find("svc:GenericMsg", ns))
    num_of_item = text_or_empty(result.find("svc:numOfItem", ns))

    items: list[dict] = []
    info_list = result.find("svc:consoleSystemFileInfoList", ns)
    if info_list is not None:
        for entry in info_list.findall("svc:ConsoleSystemFileInfo", ns):
            items.append(
                {
                    "sConsoleKey": text_or_empty(entry.find("svc:sConsoleKey", ns)),
                    "sBinaryURL": text_or_empty(entry.find("svc:sBinaryURL", ns)),
                    "sVersion": text_or_empty(entry.find("svc:sVersion", ns)),
                    "sBinaryMD5": text_or_empty(entry.find("svc:sBinaryMD5", ns)),
                    "sOptional": text_or_empty(entry.find("svc:sOptional", ns)),
                }
            )

    return {
        "GenericMsg": generic,
        "numOfItem": num_of_item,
        "consoleSystemFileInfoList": items,
        "raw_xml": xml_text,
    }


def parse_generic_msg(node: ET.Element | None) -> dict:
    if node is None:
        return {}
    fields = [
        "bIsSuccess",
        "unErrCode",
        "sErrKey",
        "sSuccessMsg",
        "sFailMsg",
        "sSuccessValue",
        "sExtraInfo1",
        "sDBServer",
        "sRemoteIP",
        "sExeDateTime",
        "sDisplayMessage",
        "sOTMessageKey",
    ]
    return {field: text_or_empty(node.find(f"{{{SERVICE_NS}}}{field}")) for field in fields}


def text_or_empty(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text


def ensure_ok_manifest(manifest: dict) -> None:
    generic = manifest.get("GenericMsg", {})
    if generic.get("bIsSuccess") == "true":
        return
    err_key = generic.get("sErrKey") or "unknown"
    fail_msg = generic.get("sFailMsg") or "request failed"
    err_code = generic.get("unErrCode") or "?"
    raise RuntimeError(f"service error {err_code} {err_key}: {fail_msg}")


def sanitize_name(name: str) -> str:
    name = name.strip().replace("\\", "_").replace("/", "_")
    return name or "unnamed"


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def parse_expected_md5s(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split("|") if part.strip()]


def download_file(
    session: requests.Session,
    item: dict,
    out_dir: Path,
    timeout: int,
    allow_bad_md5: bool,
    verify: bool,
) -> dict:
    url = item["sBinaryURL"]
    file_name = sanitize_name(item["sConsoleKey"]) or sanitize_name(Path(url).name)
    target = out_dir / file_name

    response = session.get(url, timeout=timeout, verify=verify)
    response.raise_for_status()
    data = response.content

    actual_md5 = md5_hex(data)
    expected_md5 = item.get("sBinaryMD5", "")
    expected_md5s = parse_expected_md5s(expected_md5)
    md5_ok = (not expected_md5s) or actual_md5.lower() in expected_md5s
    if not md5_ok and not allow_bad_md5:
        raise RuntimeError(
            f"MD5 mismatch for {file_name}: expected {expected_md5}, got {actual_md5}"
        )

    target.write_bytes(data)
    return {
        "file_name": file_name,
        "path": str(target),
        "size": len(data),
        "expected_md5": expected_md5,
        "expected_md5s": expected_md5s,
        "actual_md5": actual_md5,
        "md5_ok": md5_ok,
        "url": url,
        "sVersion": item.get("sVersion", ""),
        "sOptional": item.get("sOptional", ""),
    }


def dump_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def iter_items(manifest: dict) -> Iterable[dict]:
    items = manifest.get("consoleSystemFileInfoList", [])
    if not isinstance(items, list):
        return []
    return items


def main() -> int:
    args = parse_args()
    token = resolve_token(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = "MobiGoSystemFileDownloader/1.0"
    verify = not args.insecure
    if not verify:
        urllib3.disable_warnings()

    manifest = request_manifest(
        session=session,
        token=token,
        pid=args.pid,
        country=args.country,
        lang=args.lang,
        timeout=args.timeout,
        verify=verify,
    )
    dump_json(out_dir / "manifest.json", manifest)
    (out_dir / "manifest.xml").write_text(manifest["raw_xml"], encoding="utf-8")

    ensure_ok_manifest(manifest)
    items = list(iter_items(manifest))
    print(f"manifest items={len(items)}")

    if args.manifest_only:
        return 0

    downloaded: list[dict] = []
    for index, item in enumerate(items, start=1):
        console_key = item.get("sConsoleKey", "")
        print(f"[{index}/{len(items)}] downloading {console_key}")
        result = download_file(
            session=session,
            item=item,
            out_dir=out_dir,
            timeout=args.timeout,
            allow_bad_md5=args.allow_bad_md5,
            verify=verify,
        )
        downloaded.append(result)
        print(
            f"  saved {result['file_name']} size={result['size']} md5={result['actual_md5']}"
        )

    dump_json(out_dir / "downloads.json", downloaded)
    print(f"downloaded {len(downloaded)} file(s) into {out_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except requests.RequestException as exc:
        print(f"network error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
