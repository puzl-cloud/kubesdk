#!/usr/bin/env python3
import argparse
import sys
import asyncio
from typing import Dict, List
from pathlib import Path

from kubesdk_cli.k8s_dataclass_generator import prepare_module, generate_dataclasses_from_url, \
    write_inits, generate_dataclasses_from_dir, write_base_resource_py, finalize_module_init


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
    ap.add_argument(
        "--output", required=True, help="Directory to save generated dataclasses (root of the generated Python module)")
    ap.add_argument(
        "--url", help="Kubernetes cluster endpoint to take OpenAPI schema from your own cluster")
    ap.add_argument(
        "--http-headers", action="extend", nargs="+", default=[],
        help="Extra headers to use with --url: 'Authorization: Bearer some-token' (repeatable)")
    ap.add_argument(
        "--skip-tls", action="store_true", help="Disable TLS verification to use with --url")
    ap.add_argument(
        "--module-name", default="kube_models", help="Name of the generated module (will be used for all imports)")
    args = ap.parse_args()

    assert args.url or args.from_dir, \
        "You must either pass --url of your Kubernetes endpoint or --from-dir with the downloaded OpenAPI schema"

    headers = parse_headers(args.http_headers) if args.http_headers else {}
    from_dir = Path(args.from_dir).expanduser().resolve() if args.from_dir else None
    module_name = args.module_name
    models_path = Path(args.output).expanduser().resolve()
    templates_path = Path(__file__).expanduser().resolve().parent / "templates"
    extra_globals = [
        "loader.py",
        "const.py",
        "_resource_list_generics.py",
        "_resource_list_pep695.py",
        "resource.py"
    ]
    prepare_module(models_path, templates_path, extra_globals)
    if args.url:
        asyncio.run(generate_dataclasses_from_url(
            args.url, module_name=module_name, output=models_path, templates=templates_path, http_headers=headers))
    else:
        asyncio.run(generate_dataclasses_from_dir(
            from_dir, module_name=module_name, output=models_path, templates=templates_path))
    write_inits(models_path, extra_globals)
    write_base_resource_py(models_path, module_name, meta_version="v1")
    finalize_module_init(models_path, templates_path)


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
