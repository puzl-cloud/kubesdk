#!/usr/bin/env python3
import argparse
import sys
import asyncio
from typing import Dict, List
from pathlib import Path

from .dataclass_generator import prepare_module, generate_dataclasses_from_url, write_inits_with_type_loader


def parse_headers(header_list: List[str]) -> Dict[str, str]:
    headers = {}
    for raw in header_list:
        k, sep, v = raw.partition(":")
        if not sep:
            raise SystemExit(f"Bad --http-header (use 'Name: value'): {raw!r}")
        headers[k.strip()] = v.strip()
    return headers


def cli() -> None:
    ap = argparse.ArgumentParser(description="Generate dataclasses from Kubernetes OpenAPI v3 schema")
    ap.add_argument(
        "--from-dir", help="Directory with downloaded Kubernetes OpenAPI schema. You can take it for the needed version"
                           "here https://github.com/kubernetes/kubernetes/tree/release-1.34/api/openapi-spec")
    ap.add_argument("--output", default="./models", help="Directory to save generated dataclasses")
    ap.add_argument("--url", help="Kubernetes cluster endpoint to take OpenAPI schema from your own cluster")
    ap.add_argument("--http-headers", action="extend", nargs="+", default=[],
                    help="Extra headers to use with --url: 'Authorization: Bearer some-token' (repeatable)")
    ap.add_argument("--skip-tls", action="store_true", help="Disable TLS verification to use with --url")
    args = ap.parse_args()

    headers = parse_headers(args.http_headers) if args.http_headers else {}

    models_path = Path(args.output).resolve()
    templates_path = Path("./templates").resolve()
    extra_globals = [
        "loader.py",
        "import_all_dataclasses.py",
        "resource.py",
        "const.py"
    ]
    prepare_module(models_path, templates_path, extra_globals)
    asyncio.run(generate_dataclasses(
        url, token, out_pkg_dir=models_path, templates=templates_path))
    write_inits_with_type_loader(models_path, extra_globals)


if __name__ == "__main__":
    try:
        cli()
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted by user\n")
        exit(130)
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        exit(1)
