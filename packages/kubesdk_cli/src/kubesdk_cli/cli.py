#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import asyncio
from typing import Dict, List, Callable
from pathlib import Path

from kubesdk_cli.k8s_dataclass_generator import (
    prepare_module,
    generate_dataclasses_from_url,
    write_inits,
    generate_dataclasses_from_dir,
    write_base_resource_py,
    finalize_module_init,
)


def parse_headers(header_list: List[str]) -> Dict[str, str]:
    headers = {}
    for raw in header_list:
        k, sep, v = raw.partition(":")
        if not sep:
            raise SystemExit(f"Bad --http-headers (use 'Name: value'): {raw!r}")
        headers[k.strip()] = v.strip()
    return headers


def cmd_generate_models(args: argparse.Namespace) -> None:
    if args.url and args.from_dir:
        raise SystemExit("Use either --url or --from-dir (not both)")
    if not args.url and not args.from_dir:
        raise SystemExit(
            "You must either pass --url of your Kubernetes endpoint "
            "or --from-dir with the downloaded OpenAPI schema"
        )
    if args.url is None and (args.http_headers or args.skip_tls):
        raise SystemExit("--http-headers/--skip-tls require --url")

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
        "resource.py",
        "registry.py"
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


def cmd_generate_crd(args: argparse.Namespace) -> None:
    from kubesdk_cli.crd_generator import discover_crd_definitions, generate_crd

    if not args.from_dir:
        raise SystemExit("--from-dir is required")

    crds = discover_crd_definitions(args.from_dir)
    out_dir = Path(args.output).expanduser().resolve()

    for crd in crds:
        generate_crd(crd, out_dir=out_dir)


def make_common_generate_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--from-dir", help="Directory with source data")
    common.add_argument("--output", required=True, help="Directory to save generated artifacts")
    return common


def add_generate_models_parser(sub, common_generate: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        "models",
        parents=[common_generate],
        help="Generate Kubernetes dataclass models from OpenAPI v3 schema",
    )

    p.add_argument(
        "--url",
        help="Kubernetes cluster endpoint to take OpenAPI schema from your own cluster",
    )
    p.add_argument(
        "--http-headers",
        action="extend",
        nargs="+",
        default=[],
        help="Extra headers for --url: 'Authorization: Bearer some-token' (repeatable)",
    )
    p.add_argument(
        "--skip-tls",
        action="store_true",
        help="Disable TLS verification (only meaningful with --url)",
    )
    p.add_argument(
        "--module-name",
        default="kube_models",
        help="Name of the generated module (used for imports)",
    )

    p.set_defaults(func=cmd_generate_models)


def add_generate_crd_parser(sub, common: argparse.ArgumentParser) -> None:
    p = sub.add_parser(
        "crd",
        parents=[common],
        help="Generate CRDs from your kubesdk CustomK8sResourceDefinition classes",
    )
    p.set_defaults(func=cmd_generate_crd)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kubesdk", description="kubesdk CLI")
    top = ap.add_subparsers(dest="command", required=True)

    ap_generate = top.add_parser("generate", help="Generate: OpenAPI -> models, models -> CRDs")
    gen = ap_generate.add_subparsers(dest="artifact", required=True)

    common_generate = make_common_generate_parser()
    add_generate_models_parser(gen, common_generate)
    add_generate_crd_parser(gen, common_generate)

    return ap


def cli(argv: list[str] | None = None) -> None:
    """Entrypoint"""
    ap = build_parser()
    args = ap.parse_args(argv)

    func: Callable[[argparse.Namespace], None] | None = getattr(args, "func", None)
    if func is None:
        ap.error("No command selected")
    func(args)


if __name__ == "__main__":
    try:
        cli()
    except KeyboardInterrupt:
        sys.stderr.write("Interrupted by user\n")
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        raise SystemExit(1)
