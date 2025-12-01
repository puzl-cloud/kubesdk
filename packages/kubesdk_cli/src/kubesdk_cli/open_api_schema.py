from __future__ import annotations

import argparse
import logging
import concurrent.futures as cf
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import requests


def is_index_document(obj: Any) -> bool:
    """Detect index doc that maps labels to {'serverRelativeURL': ...} entries."""
    if not isinstance(obj, dict):
        return False
    paths = obj.get("paths")
    if not isinstance(paths, dict):
        return False
    for v in paths.values():
        if isinstance(v, dict) and "serverRelativeURL" in v:
            return True
    return False


def safe_module_name(label: str) -> str:
    # "apis/apps/v1" -> "apis_apps_v1"
    s = "".join(ch if ch.isalnum() else "_" for ch in label.strip())
    s = s.strip("_").lower() or "openapi"
    if not (s[0].isalpha() or s[0] == "_"):
        s = f"oapi_{s}"
    return s


def fetch(session: requests.Session, url: str, timeout: float) -> str:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def fetch_index_and_children(
    session: requests.Session, index_url: str, timeout: float
) -> List[Tuple[str, str, Any]]:
    """
    Return list of (label, absolute_url, parsed_obj).
    If index_url is a single schema, list has one item.
    """
    root_text = fetch(session, index_url, timeout)
    root_obj = json.loads(root_text)

    # Single document case
    if not is_index_document(root_obj):
        return [(urlparse(index_url).path.rsplit("/", 1)[-1] or "openapi", index_url, root_obj)]

    # Index with serverRelativeURL entries
    items: List[Tuple[str, str]] = []
    for label, meta in root_obj["paths"].items():
        if isinstance(meta, dict) and "serverRelativeURL" in meta:
            abs_url = urljoin(index_url, meta["serverRelativeURL"])
            items.append((label, abs_url))

    results: List[Tuple[str, str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(fetch, session, u, timeout): (label, u) for label, u in items}
        for fut in cf.as_completed(futs):
            label, u = futs[fut]
            text = fut.result()
            obj = json.loads(text)
            results.append((label, u, obj))

    # Sort for determinism
    results.sort(key=lambda x: x[0])
    return results


def download_open_api_schema(url: str, headers: Dict[str, str] = None, out_dir: Path | str = "./schema",
                             timeout: int = 120, skip_tls: bool = False) -> None:
    """
    Download an OpenAPI schema (single doc or v3 index with serverRelativeURL entries)
    into a folder. The output folder is taken from the env var OPENAPI_OUT_DIR,
    defaulting to './schemas'.

    Writes:
      - one JSON file per document
      - 'openapi_v3_manifest.json' mapping labels -> filenames and source URLs
    """

    url = f"{url.strip('/')}/openapi/v3"
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.verify = not skip_tls
    session.headers.update(headers or {})

    docs = fetch_index_and_children(session, url, timeout)

    manifest: Dict[str, Dict[str, str]] = {}
    for label, src_url, obj in docs:
        file_name = f"{safe_module_name(label)}.json"
        path = out_dir / file_name
        save_json(path, obj)
        manifest[label] = {"file": file_name, "source_url": src_url}
        logging.info(f"[ok] saved {label} -> {path}")

    save_json(out_dir / "openapi_v3_manifest.json", manifest)
    logging.info(f"[done] wrote {len(docs)} document(s) and manifest to {out_dir}")


def fetch_open_api_manifest(url: str, http_headers: Dict[str, str] = None) -> Dict:
    http_headers = http_headers or {}
    cluster_url = url.strip("/")
    idx = requests.get(f"{cluster_url}/openapi/v3", headers=http_headers)
    idx.raise_for_status()
    return idx.json()


def fetch_k8s_version(cluster_url: str, http_headers: Dict[str, str] = None) -> str:
    http_headers = http_headers or {}
    manifest = fetch_open_api_manifest(cluster_url, http_headers)
    version_path = manifest.get("paths", {}).get("version", {}).get('serverRelativeURL')
    r = requests.get(f"{cluster_url}{version_path}", headers=http_headers)
    r.raise_for_status()
    data = r.json()
    return data.get("version") or data.get("info", {}).get("version")
