# apiforge

Paste a website into a page, say what you need from it, and get back a script that runs as a **KNIME service**: a REST endpoint your automations can call.

An AI agent explores the site in a headless browser, records the site's internal API calls, writes a small module around them, and tests it against the live site. Everything runs on GitHub, and the result runs in KNIME with nothing extra to install.

## How it works

1. **The page** (GitHub Pages, in `docs/`) takes a website, a goal and a name, and starts a build.
2. **GitHub Actions** (`.github/workflows/forge.yml`) runs the build on a temporary machine:
   - the agent explores the site with headless Chromium while every request is recorded
   - the recording is filtered and redacted (no cookies or tokens reach the model)
   - the model writes `service.py`, using only Python's standard library
   - the result is tested against the live site, using no third-party packages at all, and repaired automatically if a test fails
3. **The repo** gets the result in `apis/<name>/`. The file that matters is `knime_node.py`: the service plus a fixed adapter, ready to paste into a Python Script node.
4. **KNIME** runs it. Deploy the workflow as a service on KNIME Hub and it becomes a REST endpoint with KNIME's own credentials.

## One-time setup (all on github.com)

1. **Upload the code.** Create a repo and upload everything in this folder except `.git`. On a Mac, press Cmd+Shift+. in Finder to see the dot-folders; `.github` must be included.
2. **Add your model key.** Settings → Secrets and variables → Actions → New repository secret: `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`). To pick a model, add a variable (same page, Variables tab) named `APIFORGE_MODEL`.
3. **Turn on the page.** Settings → Pages → Build and deployment: Deploy from a branch, branch `main`, folder `/docs`. After a minute it's live at `https://<you>.github.io/<repo>/`.
4. **Create a token for the page.** Settings (your profile) → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Repository access: only this repo. Permissions: **Actions: Read and write**, **Contents: Read-only**.
5. **Open the page**, enter the repo and token, and build your first API.

If the first build fails at the saving step, go to Settings → Actions → General → Workflow permissions and choose Read and write permissions.

You can also start a build without the page: Actions tab → forge → Run workflow.

## Running it in KNIME

The page shows these steps with your API's own examples once a build finishes.

1. Connect four nodes: **Container Input (JSON)** → **Python Script** → **String to JSON** → **Container Output (JSON)**.
2. In Container Input (JSON), set an example request as the default, e.g. `{"action": "search", "params": {"query": "coffee"}}`.
3. In Python Script, replace the starter code with `knime_node.py`. KNIME's bundled Python is enough.
4. In String to JSON, convert the `response` column. Run the workflow to check the output.
5. Upload the workflow to KNIME Hub, create a version, then Deploy → Create service.
6. Create an application password in your KNIME Hub profile for your automations.

Call it:

```bash
curl -X POST "https://api.<your-hub>/deployments/rest:<id>/execution" \
  -u "<app-password-id>:<app-password>" \
  -H "content-type: application/json" \
  -d '{"json-input": {"action": "search", "params": {"query": "coffee"}}}'
```

The top-level key matches the Parameter name in Container Input (JSON) (`json-input` by default). Every response is `{"ok": true, "action": ..., "count": n, "data": ...}` or `{"ok": false, "error": "..."}`. Send `{"action": "help"}` to list the actions and their parameters.

One workflow serves every action of that API. Build one workflow per API.

**Secrets for sites behind a login:** if an API's README lists `UPSTREAM_...` values, create flow variables with those names (from a Credentials Configuration node, for example) and the script passes them through.

## Running builds locally (optional)

```bash
pip install -e ".[dev]" && playwright install chromium
export OPENAI_API_KEY=sk-...
apiforge build hts-tariffs --url https://hts.usitc.gov --goal "Search HTS codes and duty rates"
apiforge run hts-tariffs help                                   # exactly what KNIME runs
apiforge run hts-tariffs search --params '{"query": "coffee"}'
apiforge test hts-tariffs                                       # re-run the live checks
```

Local-only options: `--show` to watch the agent's browser, `--mode manual` to browse yourself, `--profile ./browser-profile` to reuse a logged-in session, `--har file.har` to reuse a recording.

## Choosing a model provider

Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. If both are set, choose with `APIFORGE_PROVIDER=openai|anthropic`. `APIFORGE_MODEL` overrides the default model (`gpt-5.6-sol` / `claude-sonnet-5-5`); it must support tool calling. `OPENAI_BASE_URL` points it at Azure OpenAI, OpenRouter or another OpenAI-compatible endpoint.

## Things to know

- **Public repo, public results.** In a public repo, the generated code and build logs are visible to anyone. A private repo keeps them private, but GitHub Pages for private repos needs a paid GitHub plan.
- **KNIME Hub plan.** Service deployments need a KNIME Hub plan that runs workflows; check that yours includes them. Without one, you can still use the script inside your own workflows.
- **Check for an official API first.** For US tariffs, the USITC publishes a public REST endpoint (`hts.usitc.gov/reststop/...`) that its own site uses, so the agent should land on it directly.
- **Not every site works.** Sites with CAPTCHAs or heavy bot protection block the agent. Sites that render everything server-side have no JSON API to find. The agent never logs in, buys, posts, or solves CAPTCHAs.
- **Sites change.** Rebuild from the page's API list when an API stops working, or run `apiforge test <name>` on a schedule.
- **Respect site terms.** Some platforms (LinkedIn especially) prohibit automated access and pursue scrapers legally.
- **Browser recordings stay off GitHub.** `capture.har` holds everything the browser saw and is git-ignored. Build logs are committed and include sample data.

## Development

```bash
pytest -q
```

Tests cover the agent in real headless Chromium against a local site (with the model scripted), HAR filtering and redaction, both model providers' wire formats, the stdlib-only live test runner, the KNIME adapter against a stand-in KNIME API, the full build pipeline with a repair round, and the contract between the workflow and the page.

MIT licensed.
