# monday Code: Hosting & Deployment

monday Code is monday's managed hosting for an app's backend/server-side code — the "Host on monday code" option you'd pick in the Developer Center's Feature Deployment settings, as an alternative to pointing monday at a URL you host yourself externally. Compliant with GDPR, HIPAA, ISO 27001, SOC 2; currently free (docs note pricing may be introduced later).

## Contents
- [How it works](#how-it-works)
- [The CLI (`mapps`) and deployment](#the-cli-mapps-and-deployment)
- [Secrets & environment variables](#secrets--environment-variables)
- [Storage API](#storage-api)
- [Secure Storage — a third, distinct mechanism](#secure-storage--a-third-distinct-mechanism)
- [Logs & monitoring](#logs--monitoring)
- [Networking, domains, egress](#networking-domains-egress)
- [Limits](#limits)
- [Local development](#local-development)

## How it works

Your code is packaged into a **container via Google Cloud Buildpacks** and deployed to **Google Cloud Run** (scale-to-zero, request-driven), fronted by monday's own CLI and Developer Center tooling — this is stated almost verbatim across multiple monday docs pages ("zip your project files... create a container image using Buildpack... deploy the container to Cloud Run and expose a URL"), and it's consistent with the resource limits and runtime list below.

Runtime is selected via a `.mondaycoderc` file at your project root:
```json
{ "RUNTIME": "Python", "RUNTIME_VERSION": "3.10.1" }
```
| Language | Supported versions |
|---|---|
| Python | 3.10.x, 3.11.x, 3.12.x |
| Node.js | 18.x.x, 20.x.x, 22.x.x |
| Java | 8, 11, 17, 21 |
| Go | 1.x.x |
| PHP | 8.1.x, 8.2.x, 8.3.x |
| Ruby | 3.1.x, 3.2.x, 3.3.x |
| .NET Core | 6.x, 8.x |

The CLI validates `.mondaycoderc` against a schema and refuses to deploy on an unsupported runtime.

## The CLI (`mapps`) and deployment

Package: `@mondaycom/apps-cli`, invoked as `mapps`.

```bash
npm install -g @mondaycom/apps-cli
mapps init -t <API_TOKEN>     # or `mapps init` interactively
```
`-t/--token` is a monday API access token from the Developer Center's Authentication tab. `-l/--local` writes the config into the current project directory instead of the global location — handy when juggling multiple projects.

**`.mappsrc` holds that access token.** monday's prose docs don't spell out the storage path explicitly, but community/GitHub sources consistently report the default as `~/.config/mapps/.mappsrc` (global, per-user), with `-l` writing a project-local copy that the CLI auto-adds to `.gitignore`. Either way: **treat `.mappsrc` as a live credential, exactly like an API key — never commit it, never paste its contents anywhere.** If a project-local `.mappsrc` exists, confirm it's `.gitignore`d and not tracked before touching anything else in that repo.

Deploy:
```bash
mapps code:push                                # interactive: pick app + version, deploy
mapps code:push -i 123456                      # deploy to a specific app-version id
mapps code:push -z us|eu|au|il                  # deploy to a specific region (multi-region apps only)
mapps code:status -i <APP_VERSION_ID>           # check deployment status
mapps code:logs   -i <APP_VERSION_ID>           # stream logs
```
Other command groups: `mapps app:create|list|deploy|promote`, `mapps code:env` / `mapps code:secret` (§ below), `mapps scheduler:create|update|delete|list|run` (cron jobs), `mapps storage:export|search|remove-data` (the last one supports GDPR deletion requests), `mapps database:connection-string` (for the optional Document DB add-on), `mapps tunnel:create` (local dev, § below), `mapps manifest:export|import`.

## Secrets & environment variables

Configure via **Developer Center → your app → Host on monday → Server-side code → Environment variables tab**, or via CLI:
```bash
mapps code:env    -m set -k MONDAY_SIGNING_SECRET -v <value>
mapps code:secret -m set -k <KEY> -v <VALUE>
mapps code:env -m list-keys
mapps code:env -m delete -k <KEY>
```
There's no bulk `.env`-file upload — it's one key/value at a time via UI or CLI.

**Secrets specifically are write-only after creation** — the docs warn plainly: *"you can't retrieve secrets after creation, so store them securely when first generated."* Losing track of a secret's value means generating a new one, not looking the old one up.

**Runtime access — worth getting right, since it's the part the docs are least explicit about.** The SDKs fetch config through an API rather than guaranteeing plain OS-level env vars:

```javascript
// JS: @mondaycom/apps-sdk
import { EnvironmentVariablesManager, SecretsManager } from '@mondaycom/apps-sdk';
const envManager = new EnvironmentVariablesManager({ updateProcessEnv: true }); // writes into process.env
await envManager.get('SOME_KEY', { invalidate });
const secretsManager = new SecretsManager();
await secretsManager.get('SOME_KEY', { invalidate });
```
```python
# Python: monday_code (PyPI)
import monday_code
configuration = monday_code.Configuration(host="http://localhost:59999")  # local dev only, see below
async with monday_code.ApiClient(configuration) as api_client:
    env_api = monday_code.EnvironmentVariablesApi(api_client)
    value = await env_api.get_environment_variable(name)
    # monday_code.SecretsApi -> get_secret(name)
```
The JS SDK explicitly frames its manager as a way to pull a fresh value **without redeploying** — implying the baseline path is standard env-var injection at container start (so a value change normally needs a redeploy to take effect), with the SDK manager as the live-refresh escape hatch. No developer.monday.com page states outright "these become real `process.env`/`os.environ` entries automatically" — **verify empirically** (e.g. confirm `os.environ.get("MONDAY_SIGNING_SECRET")` actually resolves in your deployed container) rather than assuming from docs alone. The Python SDK in particular has no documented auto-populate-`os.environ` behavior — its access pattern is explicit async calls, not implicit environ population.

## Storage API

A built-in key-value store, an alternative to running your own database for small keyed blobs.

```javascript
import { Storage } from '@mondaycom/apps-sdk';
const storage = new Storage('<ACCESS_TOKEN>');
await storage.set(key, value, { previousVersion, shared });
await storage.get(key, { shared });
await storage.search(key, { cursor });   // paginated
await storage.delete(key, { shared });
```
```
# Python: monday_code.StorageApi
get_by_key_from_storage() / upsert_by_key_from_storage() / delete_by_key_from_storage()
increment_counter() / search_record()
```

**What `shared` actually controls — don't misread this as "global across accounts."** There's no cross-account storage tier; data is always segregated by `accountId` + app, full stop. `shared` instead controls **frontend vs. backend visibility within the same account**: `false` (default) is backend-only (not visible to a client-side view/widget); `true` is visible to both frontend and backend features of the same app.

Limits: key length ≤256 chars, value size ≤6 MB per key, concurrency 12 requests/sec per JWT token. No documented aggregate/account-wide storage ceiling was found beyond the per-key cap.

## Secure Storage — a third, distinct mechanism

Easy to conflate with the two above, so here's the full picture:

| Mechanism | Set by | Read via | Purpose |
|---|---|---|---|
| **Environment Variables** | Developer Center UI or `mapps code:env` | `EnvironmentVariablesManager` (JS) / `EnvironmentVariablesApi` (Python) | Deploy-time config, analogous to normal env vars |
| **Secrets** | Developer Center UI or `mapps code:secret` | `SecretsManager` (JS) / `SecretsApi` (Python) | Deploy-time config that's write-only after creation |
| **Secure Storage** | Your code, at runtime | `SecureStorage` (JS) / `SecureStorageApi` (Python) | An **encrypted key-value store your app populates programmatically** — e.g. a per-account OAuth token received at runtime, not something a developer types into the Developer Center |

```javascript
import { SecureStorage } from '@mondaycom/apps-sdk';
const secureStorage = new SecureStorage();
await secureStorage.set(key, value);
await secureStorage.get(key);
```
Limits: 7 requests/sec concurrency, 1 write/sec to the same key.

## Logs & monitoring

Developer Center → Host on monday → Server-side code → **Logs tab** (HTTP + console logs, filterable by time/keyword), or `mapps code:logs -i <APP_VERSION_ID>`. JS `Logger` class (`import { Logger } from '@mondaycom/apps-sdk'`) for structured log calls; Python SDK exposes `LogsApi.write_log()`.

**Monitoring tab**: hit count, latency, error rate, sliceable by status/path.

**Alert Policies**: three metric types — HTTP error rate (suggested 1–5% threshold), HTTP latency (suggested 2000–5000ms p95), and runtime-limit consumption (suggested alert at 80% of your daily execution-minute quota). Alerts land as items on an auto-created monday board, with native Slack integration and PagerDuty mentioned as an external target.

## Networking, domains, egress

Deployed apps get a monday-owned subdomain, e.g. `https://b325b-service-25854030-b5796b91.us.monday.app` (available in code as `context.mondayCodeHostingUrl`). **No custom-domain support was found documented** for monday-Code-hosted backends specifically (custom domains do exist elsewhere in the product, at the account/enterprise level — don't conflate the two).

**Multi-region** (public apps only — private apps are pinned to your dev account's home region): US, EU, AU, IL. Deploy per region:
```bash
mapps code:push -i <ID> -z eu
```
Env vars/secrets are configured per region. Once enabled and promoted live, **multi-region can't be disabled**.

**Outbound calls (relevant if your app calls a third-party API, e.g. a screening/enrichment provider): unrestricted by default.** The "Networking" feature is opt-in; if you don't enable it, there's no described egress allowlist gate. If you *do* enable it (Developer Center → Host on monday → Networking → Enable networking, ~5 min to provision), you get fixed region-specific static outbound IPs (useful for asking a third party to allowlist you) and can optionally turn on an outbound allowlist restricting destinations to specific IPs/CIDRs/FQDNs (no wildcards). **Trap:** activating the allowlist with zero entries blocks *all* outbound traffic — don't flip it on without populating it first.

## Limits

```
CPU:                  1 virtual CPU
Memory:                512 MiB RAM
Concurrent requests:   up to 80 per instance
Request timeout:       300 seconds
Max instances:         10 per region
Storage API:            12 requests/sec per JWT token
Secure Storage API:     7 requests/sec (1 write/sec per key)
```

**Daily runtime-execution-minute quota** (resets daily, scales with installed seats):
| App type | First 100 seats | Each additional seat |
|---|---|---|
| Marketplace apps | 1,200 min | +12 min |
| Public apps | 600 min | +6 min |
| Private apps | 450 min | +4.5 min |

A request-more-quota form exists in the Developer Center if these become limiting. Connected apps per account: unlimited marketplace apps + up to 5 private apps.

Cold starts aren't documented anywhere on developer.monday.com — given the confirmed Cloud Run architecture and scale-to-zero billing (execution minutes are only consumed while actively processing), they're architecturally plausible but not a number monday publishes; don't design around a specific cold-start figure.

## Local development

**(a) Mocking monday Code's backend services locally** — `apps-sdk-local-server` (Docker):
```bash
docker compose up   # after setting VOLUME_PATH in docker-compose.yml
# now listening on http://localhost:59999, emulating Storage/Secure Storage/Env Vars/Secrets/Queue
```
Point your SDK's `Configuration(host=...)` (Python) or equivalent at `http://localhost:59999` locally, and at the real service in production — same code path either way. Seed test values via `PUT /test/environments/{name}` and `PUT /test/secrets/{name}`. It's ephemeral/file-backed unless you mount a volume.

**(b) Exposing your local server to the internet** (needed for OAuth redirect URLs and monday webhook/action callbacks that require a real public HTTPS URL):
```bash
mapps tunnel:create -p <PORT> -a <APP_ID>   # -p defaults to 8080
```
Register the resulting tunnel URL as your OAuth redirect / action URL during development, then switch to the real production URL after deploying.

**(c) `PORT` handling.** monday's own official `quickstart-python-fastapi` template hardcodes port `8080` (both in its `Procfile` — `uvicorn main:app --host=0.0.0.0 --port=8080` — and in `main.py`) rather than reading a `PORT` env var. No developer.monday.com page documents the platform injecting a `PORT` variable the way, say, Heroku does. **This means reading `PORT` dynamically from the environment (falling back to a default) is safe and Cloud-Run-idiomatic, but isn't something monday's docs explicitly require** — it's a reasonable defensive default, not a fix for a documented gap.
