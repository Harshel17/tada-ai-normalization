"""Command line interface. Works without FastAPI and without PostgreSQL (except `ingest`).

  tada-normalize normalize --provider prismhr --entity employee \
      --tenant-id demo --company-id ACME --input employee.json --output normalized.json
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from . import service
from .adapters import ADAPTERS, ENTITIES
from .adapters.base import SourceContext
from .references import all_readiness_issues
from .security import ConfigurationError

EXIT_OK, EXIT_DATA_ERRORS, EXIT_USAGE = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tada-normalize", description="Normalize provider JSON into the canonical model."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-providers", help="print supported providers")
    commands.add_parser("check-inputs", help="check the distributed reference materials")

    for name, help_text in (
        ("normalize", "normalize a JSON file and write canonical JSON (no database)"),
        ("ingest", "normalize a JSON file and store it in PostgreSQL (needs DATABASE_URL)"),
    ):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("--provider", choices=list(ADAPTERS) + ["ukg", "ukg_ready"], required=True)
        cmd.add_argument("--entity", choices=ENTITIES, required=True)
        cmd.add_argument("--input", type=Path, required=True, help="provider JSON response")
        cmd.add_argument("--context-file", type=Path, help="JSON file holding the context fields")
        cmd.add_argument("--tenant-id")
        cmd.add_argument("--company-id", help="PrismHR clientId / isolved legalId / UKG cid")
        cmd.add_argument("--parent-company-id", help="isolved clientId")
        cmd.add_argument("--employee-id", help="for responses that do not repeat the employee id")
        cmd.add_argument("--payroll-id", help="UKG payroll id for a single pay statement")
        if name == "normalize":
            cmd.add_argument("--output", type=Path, required=True)
            cmd.add_argument(
                "--force", action="store_true", help="overwrite an existing output file"
            )
    return parser


def build_context(args: argparse.Namespace) -> SourceContext:
    data: dict = {}
    if args.context_file:
        with args.context_file.open(encoding="utf-8") as fh:
            data = json.load(fh)
    flags = {
        "tenant_id": args.tenant_id,
        "company_id": args.company_id,
        "parent_company_id": args.parent_company_id,
        "employee_id": args.employee_id,
        "payroll_id": args.payroll_id,
    }
    data.update({k: v for k, v in flags.items() if v is not None})
    try:
        return SourceContext.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{e['loc'][0]}: {e['msg'].lower()}" for e in exc.errors())
        raise ValueError(
            f"invalid context ({problems}); use --tenant-id and the other context flags"
        ) from exc


def load_payload(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh, parse_float=Decimal)  # keep money exactly as written


def print_issues(result) -> None:
    for issue in result.issues:
        where = f" at {issue.path}" if issue.path else ""
        print(
            f"{issue.severity.value.upper()} [{issue.code}]{where}: {issue.message}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list-providers":
        print("\n".join(ADAPTERS))
        return EXIT_OK
    if args.command == "check-inputs":
        issues = all_readiness_issues()
        print(json.dumps(issues, indent=2))
        return EXIT_USAGE if any(issues.values()) else EXIT_OK

    try:
        context = build_context(args)
        payload = load_payload(args.input)
        result = service.normalize(args.provider, args.entity, payload, context)
    except (
        OSError,
        ValueError,
        ConfigurationError,
    ) as exc:  # unreadable file, bad JSON/context, config
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print_issues(result)
    if args.command == "ingest":
        return _ingest(result)

    document = {"ok": result.ok, "counts": result.counts(), **result.model_dump(mode="json")}
    try:
        mode = "w" if args.force else "x"  # refuse to overwrite unless --force
        with args.output.open(mode, encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, allow_nan=False)
            fh.write("\n")
    except FileExistsError:
        print(f"error: {args.output} exists; use --force to overwrite", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"wrote {args.output}: {result.counts()}", file=sys.stderr)
    return EXIT_OK if result.ok else EXIT_DATA_ERRORS


def _ingest(result) -> int:
    from .persistence import repository as repo  # imported here so `normalize` needs no database
    from .persistence.db import make_engine, transaction

    if not result.ok:
        print("error: not storing a result that has errors", file=sys.stderr)
        return EXIT_DATA_ERRORS
    try:
        with transaction(make_engine()) as session:
            summary = repo.ingest(session, result)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not store records ({type(exc).__name__})", file=sys.stderr)
        return EXIT_DATA_ERRORS
    print(json.dumps(summary.counts, indent=2))
    return EXIT_OK
