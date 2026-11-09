"""apiforge: point it at a website, get an API you can run inside KNIME.

  apiforge build hts-tariffs --url https://hts.usitc.gov --goal "Look up HS codes and duty rates"
  apiforge run hts-tariffs search --params '{"query": "coffee"}'
  apiforge test hts-tariffs
  apiforge list
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from . import pipeline

APIS_DIR = pipeline.APIS_DIR


def cmd_build(args: argparse.Namespace) -> int:
    mode = "har" if args.har else args.mode
    try:
        meta = pipeline.build(
            args.name, args.goal, args.url,
            mode=mode,
            har_file=Path(args.har) if args.har else None,
            recapture=args.recapture,
            profile=Path(args.profile) if args.profile else None,
            headless=not args.show,
            max_steps=args.max_steps,
            model=args.model,
            max_repairs=args.max_repairs,
            verify=not args.no_verify,
            apis_dir=APIS_DIR,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    d = APIS_DIR / meta["name"]
    print(f"\nActions: {', '.join(meta['actions']) or 'none found'}")
    print(f"KNIME script: {d / 'knime_node.py'}  (paste into a Python Script node)")
    print(f"Try it here:  apiforge run {meta['name']} help")
    return 0 if meta["status"] != "failing" else 1


def _load(name: str):
    path = APIS_DIR / pipeline.slugify(name) / "knime_node.py"
    if not path.exists():
        raise SystemExit(f"No API at {path.parent}. Build it first.")
    spec = importlib.util.spec_from_file_location("forge_service", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_run(args: argparse.Namespace) -> int:
    try:
        params = json.loads(args.params) if args.params else {}
    except ValueError as exc:
        raise SystemExit(f"--params must be JSON: {exc}")
    res = _load(args.name)._forge_handle({"action": args.action, "params": params})
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0 if res.get("ok") else 1


def cmd_test(args: argparse.Namespace) -> int:
    from .verify import run_smoke_tests

    ok, log = run_smoke_tests(APIS_DIR / pipeline.slugify(args.name))
    print(log)
    return 0 if ok else 1


def cmd_list(_: argparse.Namespace) -> int:
    rows = []
    if APIS_DIR.exists():
        for d in sorted(APIS_DIR.iterdir()):
            f = d / "apiforge.json"
            if f.exists():
                m = json.loads(f.read_text())
                rows.append(f"{m['name']:24} {m['status']:11} {', '.join(m.get('actions', []))}")
    print("\n".join(rows) if rows else "No APIs yet.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apiforge", description="Point at a website, get an API for KNIME.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="explore a site and generate its API")
    b.add_argument("name", help="API name, e.g. hts-tariffs")
    b.add_argument("--goal", required=True, help="what the API should do, in plain words")
    b.add_argument("--url", help="page to start from")
    b.add_argument("--mode", choices=["agent", "manual"], default="agent",
                   help="agent: AI drives a headless browser (default); manual: you browse")
    b.add_argument("--har", help="skip browsing and use an existing .har file")
    b.add_argument("--recapture", action="store_true", help="explore again even if a capture exists")
    b.add_argument("--profile", help="browser profile dir, to reuse a logged-in session")
    b.add_argument("--show", action="store_true", help="show the agent's browser window")
    b.add_argument("--max-steps", type=int, default=30, help="agent action budget")
    b.add_argument("--model", help="override the model (or set APIFORGE_MODEL)")
    b.add_argument("--max-repairs", type=int, default=2)
    b.add_argument("--no-verify", action="store_true")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("run", help="call one action locally, exactly as KNIME would")
    r.add_argument("name")
    r.add_argument("action", help="action name, or 'help' to list them")
    r.add_argument("--params", help='JSON object, e.g. \'{"query": "coffee"}\'')
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("test", help="re-run an API's live smoke tests (detects site changes)")
    t.add_argument("name")
    t.set_defaults(func=cmd_test)

    ls = sub.add_parser("list", help="list generated APIs")
    ls.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
