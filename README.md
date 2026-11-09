# apiforge

Paste a website into the web page, say what you need from it, and press **Build API**. An AI agent explores the site in a headless browser, records its internal API calls, writes a clean REST API on top of them, tests it against the live site, and gives you an **endpoint and an API key** to use in your automations.

```bash
apiforge server        # then open http://localhost:8000
```

The page asks for your access key (the `FORGE_ADMIN_KEY` you set), then shows:

- a form for the website, what the API should do, and an optional name
- live progress while it works: exploring, finding the API, writing it, testing it
- the result: endpoint, API key (shown once, with copy buttons), routes, a curl example and KNIME instructions
- your APIs, with a button to issue a new key for any of them

Everything the page does is also available over HTTP for scripts and KNIME workflows:

```bash
curl -X POST http://localhost:8000/forge \
  -H "x-admin-key: $FORGE_ADMIN_KEY" -H "content-type: application/json" \
  -d '{"url": "https://hts.usitc.gov", "goal": "Search HTS codes by keyword or number and return duty rates"}'
# → {"job_id": "3f2a9c1b7e04", "status_url": ".../forge/jobs/3f2a9c1b7e04"}
# poll status_url until "status" is "done"; "result" holds endpoint, api_key, routes, docs
```

## How it works

1. **Explore (agent).** An AI model drives headless Chromium with HAR recording on. After every click or search it sees the page plus the API calls that action triggered, so it steers toward the features the goal needs: several searches with different values, detail pages, filters, pagination. It is told to stay read-only: no logins, purchases, posts, or CAPTCHA solving.
2. **Filter.** `har.py` drops assets and trackers, groups calls into endpoint templates (`/items/123` → `/items/{id}`), redacts cookies and auth headers, and trims bodies.
3. **Generate.** The model writes `upstream.py` (talks to the site) and `main.py` (your API, designed around the goal), plus smoke tests and docs. Only whitelisted filenames are written.
4. **Verify and repair.** The service boots and its smoke tests run against the live site. Crashes, wrong status codes or empty results go back to the model with the server log, up to 2 times.
5. **Serve.** The forge server mounts every API at `/api/<name>` with its own key, so new APIs go live without a redeploy.

## Choosing a model provider

apiforge works with **OpenAI** or **Anthropic**. Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`; if both are set, pick with `APIFORGE_PROVIDER=openai|anthropic`. Override the model with `APIFORGE_MODEL` (defaults: `gpt-5.6-sol` / `claude-sonnet-5-5`). The model must support tool calling, and strong coding models give noticeably better APIs. `OPENAI_BASE_URL` points it at any OpenAI-compatible endpoint (Azure OpenAI, OpenRouter, a local model).

## Run it

```bash
git clone https://github.com/<you>/apiforge && cd apiforge
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && playwright install chromium
cp .env.example .env   # fill in, then: set -a; source .env; set +a

apiforge server                          # forge server on :8000 (POST /forge, /api/<name>/...)
```

Or from the command line without the server:

```bash
apiforge build hts-tariffs --url https://hts.usitc.gov --goal "Search HTS codes and duty rates"
apiforge build hts-tariffs ... --show            # watch the agent's browser
apiforge build my-site ... --mode manual         # you browse instead of the agent
apiforge build my-site ... --profile ./browser-profile   # reuse a logged-in session
apiforge serve hts-tariffs                       # run one API alone (docs at /docs)
apiforge test hts-tariffs                        # re-run live checks (catches site changes)
apiforge list
```

## Deploy the forge server

The web page is served by the forge server itself, so wherever the server runs, the page is at its root address. The root `Dockerfile` includes Chromium. It needs a persistent volume at `/data` (generated APIs and key hashes live there) and these env vars: `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`), `FORGE_ADMIN_KEY`, `PUBLIC_URL`.

```bash
docker build -t apiforge .
docker run -p 8000:8000 -v apiforge-data:/data --env-file .env apiforge
```

Any host that runs a Docker container or Python works: a company server, a VM on AWS/Azure/Google Cloud, or a PaaS. Attach persistent storage at `/data`, set the env vars, and give it at least 2 GB of RAM for the browser. Put it behind HTTPS before using it over the internet, since keys travel in headers. Each generated API also has its own Dockerfile in `apis/<name>/` if you'd rather deploy one standalone.

## Using it from KNIME

- **Call an API:** add a **GET Request** node with the URL `https://<server>/api/<name>/<route>?…`, and on its Request Headers tab add `x-api-key` with your key. Feed the response into **JSON to Table**.
- **Forge a new API from a workflow:** a **POST Request** node to `/forge` with the JSON body above and header `x-admin-key`, then poll `status_url` with a GET Request node in a loop until `status` is `done`, and read `endpoint` and `api_key` from the result.
- Store keys in KNIME credentials (Credentials Configuration node) rather than typing them into node settings.

## Things to know

- **Check for an official API first.** For US tariffs, the USITC has a public REST endpoint (`hts.usitc.gov/reststop/...`) that the site itself uses, so the agent should land on it directly.
- **Not every site works headless.** Sites with heavy bot protection (CAPTCHAs, Cloudflare challenges) will block the agent; the job log says so. Sites that render everything server-side have no JSON API to find.
- **Logins.** The agent never logs in. For sites behind a login, log in once with `--mode manual --profile ./browser-profile`, then build with the same `--profile`. Upstream secrets go in env vars (`UPSTREAM_COOKIE`, etc.) and are never written into code.
- **Sites change.** Run `apiforge test <name>` on a schedule and rebuild when it fails.
- **Respect site terms.** Some platforms (LinkedIn especially) ban automated access and sue scrapers.
- **Captures contain personal data** and are git-ignored; keep it that way. Review generated code before relying on it.
- **Jobs live in memory**, so job status is lost on restart (the built APIs and keys persist).

## Roadmap

- Scheduled health checks with automatic rebuild when an API breaks.
- Change tracking: store responses over time and expose `/changes` (useful for tariff updates).
- Web UI for forging and managing APIs.
- A KNIME component that wraps forge + poll into one node.

## Development

```bash
pytest -q
```

Tests run the agent against a local site in real headless Chromium (with the model scripted), plus HAR filtering and redaction, the file whitelist, the live smoke-test runner, the full build pipeline with a repair round, and the forge server (mounting, per-API keys, the job flow).

MIT licensed.
