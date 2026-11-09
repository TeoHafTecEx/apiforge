"""apiforge: point it at a website, get a deployable REST API for it.

  apiforge build hts-tariffs --url https://hts.usitc.gov --goal "Look up HS codes and duty rates"
  apiforge build my-site --url https://... --goal "..." --mode manual   # you browse instead
  apiforge serve hts-tariffs        # run one API locally
  apiforge server                   # run the forge server (all APIs + POST /forge)
  apiforge list
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
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
    name = meta["name"]
    if not args.no_key:
        from . import keys

        key = keys.issue(APIS_DIR, name)
        print(f"\nAPI key for the forge server (shown once): {key}")
    print(f"Run it alone:  apiforge serve {name}")
    print(f"Run all APIs:  apiforge server   ->  /api/{name}/...  with header x-api-key")
    return 0 if meta["status"] != "failing" else 1


def cmd_serve(args: argparse.Namespace) -> int:
    d = APIS_DIR / pipeline.slugify(args.name)
    if not (d / "main.py").exists():
        raise SystemExit(f"No service at {d}. Build it first.")
    print(f"Serving {d} on http://{args.host}:{args.port}  (docs at /docs)")
    return subprocess.call(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", args.host, "--port", str(args.port)],
        cwd=d,
    )


def cmd_server(args: argparse.Namespace) -> int:
    if not os.getenv("FORGE_ADMIN_KEY"):
        print("Warning: FORGE_ADMIN_KEY is not set, so /forge and /apis are disabled.")
    import uvicorn

    uvicorn.run("apiforge.server:app", host=args.host, port=args.port)
    return 0


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
                rows.append(f"{m['name']:24} {m['status']:11} {m['built_at']}  {m['goal']}")
    print("\n".join(rows) if rows else "No APIs yet.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apiforge", description="Point at a website, get a deployable API.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="explore a site and generate an API service")
    b.add_argument("name", help="service name, e.g. hts-tariffs")
    b.add_argument("--goal", required=True, help="what the API should do, in plain words")
    b.add_argument("--url", help="page to start from")
    b.add_argument("--mode", choices=["agent", "manual"], default="agent",
                   help="agent: AI drives a headless browser (default); manual: you browse")
    b.add_argument("--har", help="skip browsing and use an existing .har file")
    b.add_argument("--recapture", action="store_true", help="explore again even if a capture exists")
    b.add_argument("--profile", help="browser profile dir, to reuse a logged-in session")
    b.add_argument("--show", action="store_true", help="show the agent's browser window")
    b.add_argument("--max-steps", type=int, default=30, help="agent action budget")
    b.add_argument("--model", help="override the Claude model")
    b.add_argument("--max-repairs", type=int, default=2)
    b.add_argument("--no-verify", action="store_true")
    b.add_argument("--no-key", action="store_true", help="don't issue a forge-server API key")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("serve", help="run one generated API locally")
    s.add_argument("name")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    srv = sub.add_parser("server", help="run the forge server: all APIs + POST /forge")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    srv.set_defaults(func=cmd_server)

    t = sub.add_parser("test", help="re-run an API's live smoke tests (detects site changes)")
    t.add_argument("name")
    t.set_defaults(func=cmd_test)

    ls = sub.add_parser("list", help="list generated APIs")
    ls.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
