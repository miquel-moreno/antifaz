# Install guide

[Leer en español](instalacion.md)

This guide is for a company that wants to run Antifaz on its own machine or server, and point its tools (OpenAI or Anthropic SDKs, curl, Claude Code) at it.

Remember: Antifaz pseudonymises. It only protects the personal data it detects. Test it with your own kind of texts before you use it with real data.

> [!IMPORTANT]
> **There are two paths. Do not mix them.**
>
> - **[Section 2: Install the published v0.1.1 (today)](#2-install-the-published-v011-today).** Every command there works with the published image `ghcr.io/miquel-moreno/antifaz:0.1.1`.
> - **[Section 3: Coming in v0.2.0 (only on `main` today)](#3-coming-in-v020-only-on-main-today).** `init`, `doctor`, `setup claude-code`, the panel and the Compose file of the release. v0.2.0 is not released yet. The 0.1.1 image does not have these commands: it answers "invalid arguments".

> [!NOTE]
> Screenshots will be added after the design prototype (#30).

## Contents

1. [Requirements](#1-requirements)
2. [Install the published v0.1.1 (today)](#2-install-the-published-v011-today)
3. [Coming in v0.2.0 (only on `main` today)](#3-coming-in-v020-only-on-main-today)
4. [If Antifaz is down, and how to switch it off](#4-if-antifaz-is-down-and-how-to-switch-it-off)
5. [Claude Code traffic that does not go through Antifaz](#5-claude-code-traffic-that-does-not-go-through-antifaz)
6. [What Antifaz blocks: images, PDFs and files](#6-what-antifaz-blocks-images-pdfs-and-files)
7. [Upgrade, clean backups and rotate keys](#7-upgrade-clean-backups-and-rotate-keys)
8. [Troubleshooting](#8-troubleshooting)
9. [What is not tested yet](#9-what-is-not-tested-yet)

## 1. Requirements

To run the gateway (section 2):

- **Docker with Compose.** Docker Desktop on Windows or macOS, or Docker Engine with the Compose plugin on Linux. Check it with `docker compose version`.
- **git**, to get the Compose file and the example settings file.
- The image is only tested on `linux/amd64` (Intel or AMD processors). It is not tested on ARM (for example Apple Silicon or Raspberry Pi).
- A key of at least one provider: OpenAI, Anthropic, or both.

Only for section 3 (v0.2.0 features, from a clone of `main`): **Python 3.12** and **[uv](https://docs.astral.sh/uv/)**.

## 2. Install the published v0.1.1 (today)

Everything in this section works with the published image `ghcr.io/miquel-moreno/antifaz:0.1.1`. Nothing here needs section 3.

Antifaz listens only on `127.0.0.1` (this machine). Other machines cannot reach it. To use it from other machines, see [2.7](#27-use-it-from-other-machines).

### 2.1 Get the files

The v0.1.1 release has no Compose file. Use the one in the repository: it starts the published image `ghcr.io/miquel-moreno/antifaz:0.1.1`. The clone also has the example settings file, `.env.example`.

Linux or macOS (bash):

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
```

Windows (PowerShell):

```powershell
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
```

### 2.2 Write the `.env` file

The `.env` file holds the keys. Antifaz reads it when the container starts. Never commit it to git and never send it by email or chat.

Copy the example file. bash:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Then open `.env` in a text editor and change:

- `ANTIFAZ_API_KEY`: a random value of at least 32 characters. Your clients use it. To make one:
  - bash: `openssl rand -hex 32`
  - PowerShell: `$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })`
- `ANTIFAZ_OPENAI_API_KEY` and/or `ANTIFAZ_ANTHROPIC_API_KEY`: your provider keys. Delete the line of a provider you do not use (its routes answer 503).
- `ANTIFAZ_ADMIN_TOKEN`: **generate it or delete the line.** Version 0.1.1 ignores this line. It is for the browser panel, which arrives with v0.2.0. But v0.2.0 **will not start** while it still has the example value (`change-me...`). So do one of these now:
  - make another random value, the same way as above, **different from** `ANTIFAZ_API_KEY`; or
  - delete the line. Then the panel stays off in v0.2.0.
- `ANTIFAZ_TRUSTED_PROXIES`: leave it empty. Version 0.1.1 ignores it (it is for the v0.2.0 panel behind a reverse proxy).

Rules for the values: no spaces, no quotes and no `$` (Compose would try to replace it). On Windows, save the file as **UTF-8** (see [Troubleshooting](#8-troubleshooting)).

Antifaz refuses to start while `ANTIFAZ_API_KEY` or a provider key still has the example value (`change-me...`).

### 2.3 Start Antifaz

In the folder of the clone (same command in bash and PowerShell):

```bash
docker compose up -d
```

It pulls `ghcr.io/miquel-moreno/antifaz:0.1.1`. Check it:

```bash
docker compose ps                    # the "antifaz" service should be "healthy" after a few seconds
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"0.1.1"}
```

In PowerShell, use `curl.exe` instead of `curl`:

```powershell
docker compose ps
curl.exe http://127.0.0.1:8000/healthz
```

If the service shows `restarting`, Antifaz refused the configuration. Read why:

```bash
docker compose logs antifaz
```

It names the variable to fix, never its value. Fix `.env` and run `docker compose up -d` again.

### 2.4 Without Compose: one `docker run`

The same hardening as the Compose file, in one command (bash, in the folder with `.env`):

```bash
docker run -d --name antifaz --env-file .env -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp:size=16m,mode=1777,noexec,nosuid,nodev \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 128 --restart unless-stopped \
  ghcr.io/miquel-moreno/antifaz:0.1.1
```

PowerShell (each line ends with a backtick `` ` ``):

```powershell
docker run -d --name antifaz --env-file .env -p 127.0.0.1:8000:8000 `
  --read-only --tmpfs /tmp:size=16m,mode=1777,noexec,nosuid,nodev `
  --cap-drop ALL --security-opt no-new-privileges `
  --memory 512m --cpus 1 --pids-limit 128 --restart unless-stopped `
  ghcr.io/miquel-moreno/antifaz:0.1.1
```

Use this **or** `docker compose up -d`, not both: they use the same port. The rest of this guide uses Compose.

### 2.5 Prove that no data leaves with `verify`

`verify` uses **your** configuration, plants fake personal data (DNI, NIE, IBAN, email, phone, card) in every part of a request, and fails if one value reaches a fake provider. It never calls a real provider and costs nothing.

```bash
docker compose exec antifaz antifaz verify
```

A good result starts with `antifaz verify: PASS`. Exit code: 0 pass, 1 a check failed, 2 the configuration cannot start.

### 2.6 Point your clients at Antifaz

Your clients use the **Antifaz key** (`ANTIFAZ_API_KEY`), not the provider key. The provider keys stay in `.env`.

**OpenAI SDK (Python):**

```python
import os

from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key=os.environ["ANTIFAZ_API_KEY"])
```

**Anthropic SDK (Python):** without `/v1` at the end.

```python
import os

from anthropic import Anthropic

client = Anthropic(base_url="http://127.0.0.1:8000", api_key=os.environ["ANTIFAZ_API_KEY"])
```

**curl** (bash), with fake data:

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $ANTIFAZ_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "My DNI is 12345678Z"}]}'
```

PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Headers @{ "Authorization" = "Bearer $env:ANTIFAZ_API_KEY" } -ContentType "application/json" `
  -Body '{"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "My DNI is 12345678Z"}]}'
```

The provider receives `[[ES_DNI_1]]`, and the answer comes back with `12345678Z`.

For Claude Code, see [3.4](#34-claude-code-setup-claude-code).

### 2.7 Use it from other machines

Antifaz speaks plain HTTP and listens only on `127.0.0.1`. To use it from other machines:

1. Put a reverse proxy with HTTPS in front (Caddy, nginx, Traefik). Do not publish the port on `0.0.0.0`.
2. Add the name that clients use to `ANTIFAZ_ALLOWED_HOSTS` in `.env` (for example `antifaz.example.internal`), then run `docker compose up -d --force-recreate`.
3. The proxy must not save request bodies in its logs: they hold the personal data before it is masked.

For the panel behind a proxy (v0.2.0), see [3.5](#35-the-panel).

More details in [TECNICO.md](TECNICO.md#proxy-inverso-https) (in Spanish).

## 3. Coming in v0.2.0 (only on `main` today)

> [!WARNING]
> **Not released yet.** Nothing in this section works with the 0.1.1 image: `docker compose exec antifaz antifaz doctor` answers "invalid arguments", and the release download links below give "Not Found" until v0.2.0 is out. The steps are tested end to end with an image built from `main`. For today, use [section 2](#2-install-the-published-v011-today).

### 3.1 The Compose file of the release

Each release from v0.2.0 has a `docker-compose.yml` file that pins the image by digest (the exact image that was built, tested and signed). No clone needed. In an empty folder:

Linux or macOS (bash):

```bash
mkdir antifaz && cd antifaz
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/v0.2.0/docker-compose.yml
```

Windows (PowerShell). Use `curl.exe`, not `curl` (in PowerShell 5.1, `curl` is another command):

```powershell
mkdir antifaz; cd antifaz
curl.exe -LO https://github.com/miquel-moreno/antifaz/releases/download/v0.2.0/docker-compose.yml
```

### 3.2 Write `.env` with `antifaz init`

In the container, no clone needed. It asks for your provider keys without showing them, writes `.env`, and makes a new random `ANTIFAZ_API_KEY`. It shows that key **once**: copy it, your clients use it. No key goes on the command line.

Linux or macOS (bash):

```bash
docker run --rm -it -v "$PWD:/work" -w /work --user "$(id -u):$(id -g)" --network none \
  ghcr.io/miquel-moreno/antifaz:0.2.0 init
```

Windows (PowerShell), in one line and without `--user`:

```powershell
docker run --rm -it -v "${PWD}:/work" -w /work --network none ghcr.io/miquel-moreno/antifaz:0.2.0 init
```

Why each option:

- `--rm`: deletes the container when it ends. Docker keeps the output of a container (with your new key) while the container exists.
- `-it`: `init` needs a terminal to ask for the keys. Without it, `init` stops with "no terminal to ask the questions".
- `--network none`: `init` does not use the network.
- `--user` (Linux only): the new `.env` belongs to you, not to the user of the container. Without it, you may not be able to read the file.
- To skip a provider, press Enter when `init` asks for its key. Its routes then answer 503.
- If a `.env` already exists, `init` asks you to type `yes`, and keeps a copy called `.env.bak-YYYYMMDD-HHMMSS`.
- For scripts and CI there is `--non-interactive` with `--openai-key-env VAR` or `--anthropic-key-env VAR` (the **name** of an environment variable, never the key). See `antifaz init --help`.
- `init` also always writes `ANTIFAZ_ADMIN_TOKEN`, a second random value for the panel (see [3.5](#35-the-panel)). It is shown once, like the key, and it is **not** the key your clients use.

> In Git Bash on Windows, use PowerShell for this command: Git Bash changes paths like `/work` and the command fails.

Then start it with `docker compose up -d`, as in [2.3](#23-start-antifaz).

### 3.3 Check it with `doctor`

`doctor` checks the configuration and the running gateway. It names the setting to fix, never its value, and lists the next steps.

```bash
docker compose exec antifaz antifaz doctor
```

To also test your provider keys, add `--providers`. It only asks each provider for its list of models, which is free:

```bash
docker compose exec antifaz antifaz doctor --providers
```

Inside the container `doctor` says "no .env in that folder: only environment variables were read". That is normal: in the container the settings come from the environment, the same ones the gateway uses.

Exit code: 0 if every check passed, 1 if something failed.

### 3.4 Claude Code: `setup claude-code`

On the computer where you use Claude Code, from a clone of `main` (with [uv](https://docs.astral.sh/uv/)) until v0.2.0 is out. This command runs on your computer, not in the container.

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
uv sync
uv run antifaz setup claude-code            # shows the change; writes nothing
uv run antifaz setup claude-code --apply    # writes it (asks you to type yes, keeps a backup)
```

- It only sets `ANTHROPIC_BASE_URL` in your Claude Code user settings: `~/.claude/settings.json` (on Windows, `%USERPROFILE%\.claude\settings.json`). With `--project` it writes `.claude/settings.local.json` in the current folder instead.
- It **never** writes a key. Put your Antifaz key in the `ANTHROPIC_AUTH_TOKEN` environment variable:
  - bash or zsh: add this line to `~/.bashrc` or `~/.zshrc` with a text editor, then open a new terminal:

    ```bash
    export ANTHROPIC_AUTH_TOKEN=<your ANTIFAZ_API_KEY>
    ```

  - PowerShell (permanent for your Windows user; open a new terminal after):

    ```powershell
    [Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", "<your ANTIFAZ_API_KEY>", "User")
    ```

    A key typed at a prompt stays in the shell history. You can also set it in Windows: "Edit environment variables for your account".
- Claude Code then runs on the Anthropic API key in Antifaz's `.env`, billed per token to that account, not on a Claude Pro or Max subscription. Signing in with a Claude subscription does not go through Antifaz.
- Restart Claude Code. `/status` should show the line "Anthropic base URL" with Antifaz's address.
- If Antifaz runs on another address, add `--url`, for example `--url https://antifaz.example.internal`.
- It configures the Claude Code CLI. The VS Code extension and the desktop app read their own settings ([Claude Code docs](https://code.claude.com/docs/en/llm-gateway-connect)); this command does not change them.

### 3.5 The panel

A browser panel at `http://localhost:8000/panel` is work in progress (issue #53). It opens with `ANTIFAZ_ADMIN_TOKEN`, not with the key your clients use.

- **To turn the panel off**, delete the `ANTIFAZ_ADMIN_TOKEN` line from `.env` and restart (`docker compose up -d --force-recreate`).
- Behind a reverse proxy, put the **exact IP address of the proxy**, as Antifaz sees it, in `ANTIFAZ_TRUSTED_PROXIES` (one address, for example `172.20.0.10`). In Compose, give the proxy container a fixed address (`ipv4_address` under its network) so it does not change. Only then does the panel believe the proxy's `X-Forwarded-For` and `X-Forwarded-Proto`.
  - **Never** put the Docker gateway (`172.x.0.1`) or the whole Docker network: requests that come in through the published port arrive from the gateway, so any client could then fake those headers. Never `0.0.0.0/0`. `antifaz doctor` warns about ranges wider than /24.
  - The proxy must add `X-Forwarded-For`. If it does not, every client looks like the proxy and they all share one login limit (5 failures a minute).
  - Without a proxy, leave it empty.
- It works on `http://localhost:8000/panel` (also `127.0.0.1` or `[::1]`) or behind HTTPS; over plain HTTP from any other address it shows a page that asks for HTTPS and does not let you log in.
- The proxy must **keep the original `Host`** (nginx: `proxy_set_header Host $host;`; Caddy does it by default). If it rewrites it, the panel login answers 403.
- In Docker **without** a proxy, every request arrives from the same address, so the login limit (5 failures a minute) is shared by everyone.
- The same happens behind a trusted proxy that does **not** add `X-Forwarded-For`, or behind a CDN that is not in `ANTIFAZ_TRUSTED_PROXIES`: every client looks like the proxy, and anyone can lock the administrator out for a minute with 5 wrong tokens. Make the proxy add `X-Forwarded-For`.
- Two Antifaz on different `localhost` ports (for example 8000 and 8001) overwrite each other's panel session: browsers do not keep cookies apart by port. Log in again, or use one of them at a time.

## 4. If Antifaz is down, and how to switch it off

**If Antifaz is down**, your client gets a connection error and **nothing is sent** to the provider. The client does not go to the provider directly by itself. In Claude Code you see an error like "Connection refused" or "Unable to connect to API".

**To switch it off in a minute** (go back to the provider directly):

1. Claude Code: remove the setting, from the clone (see [3.4](#34-claude-code-setup-claude-code)):

   ```bash
   uv run antifaz setup claude-code --uninstall --apply
   ```

   If you used `--project` or `--url` when you installed it, add them again here. It only removes the value if nobody changed it.
2. Remove the Antifaz key from your environment:
   - bash or zsh: delete the `export ANTHROPIC_AUTH_TOKEN=...` line from `~/.bashrc` or `~/.zshrc`, and run `unset ANTHROPIC_AUTH_TOKEN`.
   - PowerShell: `[Environment]::SetEnvironmentVariable("ANTHROPIC_AUTH_TOKEN", $null, "User")`, then open a new terminal.
3. Restart Claude Code. For your own SDK code, change `base_url` and the key back.
4. Stop the gateway, in its folder:

   ```bash
   docker compose down
   ```

## 5. Claude Code traffic that does not go through Antifaz

With `ANTHROPIC_BASE_URL`, Claude Code sends its **model requests** to Antifaz. Some other requests go **directly** to Anthropic or to other services. Antifaz does not see them and does not mask them. According to the Claude Code documentation ([gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol), [connect to a gateway](https://code.claude.com/docs/en/llm-gateway-connect)):

- **Telemetry and other non-essential traffic** (version checks, release notes and similar) goes to Anthropic and other services such as GitHub. Since Claude Code v2.1.246, it does not carry your gateway key.
- **The fast mode check** calls `api.anthropic.com` directly.
- **The WebFetch domain safety check** calls `api.anthropic.com` directly before fetching a page.

To switch off the non-essential traffic, set this variable next to `ANTHROPIC_AUTH_TOKEN`:

```bash
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

```powershell
[Environment]::SetEnvironmentVariable("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1", "User")
```

What you lose (trade-off):

- **Automatic updates stop.** Update Claude Code in another way (your package manager, or your company's software tool).
- **Fast mode** (`/fast`) says it is not available.
- **It does not stop the WebFetch check.** To stop it too, add `"skipWebFetchPreflight": true` to your Claude Code settings.

Also, with a gateway key, Remote Control and voice dictation in Claude Code are not available (they need a claude.ai login).

## 6. What Antifaz blocks: images, PDFs and files

Antifaz cannot look for personal data inside an image, a PDF or a file. So it **blocks** any request that carries one, instead of sending it unchecked ("fail-closed", [ADR-0006](adr/0006-campos-desconocidos.md), in Spanish). The client gets:

```json
{"error": {"code": "antifaz_blocked", "message": "attachments are not supported; request blocked"}}
```

with HTTP status 400. The same happens with file ids, documents, citations and Anthropic server tools (like web search).

What this means for Claude Code users:

- **Pasting a screenshot** or asking Claude Code to read an image or a PDF makes that request fail with this error. Nothing is sent to the provider.
- The image stays in the conversation, so the next messages will probably fail too. Start a new conversation with `/clear`.
- Copy the text you need as text instead of a screenshot.

This behaviour comes from the gateway tests. It is not checked with a real Claude Code session yet (see [section 9](#9-what-is-not-tested-yet)).

## 7. Upgrade, clean backups and rotate keys

### Upgrade

Read the [CHANGELOG](../CHANGELOG.md) of the new version first: a "Breaking" line means you must change something.

**With 0.1.1 (the clone, section 2)**: `git pull`, then `docker compose pull` and `docker compose up -d`. The Compose file of the repository uses the last published version. To choose one, set `ANTIFAZ_VERSION` (for example `ANTIFAZ_VERSION=0.1.1 docker compose up -d` in bash).

**[v0.2.0 and later]** With the release Compose file, in its folder (change `vX.Y.Z` to the new version):

```bash
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/vX.Y.Z/docker-compose.yml
docker compose pull
docker compose up -d
docker compose exec antifaz antifaz doctor
```

In PowerShell, `curl.exe` instead of `curl`. Your `.env` stays as it is. To go back, download the Compose file of the old version and run the same commands.

From 0.1.x to 0.2.0 the image starts with `antifaz serve` instead of `uvicorn ...`. The Compose file and the `docker run` command of [2.4](#24-without-compose-one-docker-run) keep working. A `docker run IMAGE <uvicorn options>` no longer works. Before you move to 0.2.0, check `ANTIFAZ_ADMIN_TOKEN` in `.env` (see [2.2](#22-write-the-env-file)): with the example value, 0.2.0 does not start.

### Clean backups

Antifaz keeps a copy every time it replaces a file. **The copies hold your old keys or settings.** Delete them when you do not need them:

- `.env.bak-YYYYMMDD-HHMMSS`, next to `.env` (made by `init`, v0.2.0):
  - bash: `rm .env.bak-*`
  - PowerShell: `Remove-Item .env.bak-*`
- `settings.json.bak-YYYYMMDD-HHMMSS`, next to `~/.claude/settings.json` (made by `setup claude-code`).
- With `--project`: `~/.claude/antifaz-backups/` (on Windows, `%USERPROFILE%\.claude\antifaz-backups\`).

`setup claude-code` prints the full path of each copy when it writes it.

### Rotate keys

**A provider key** (OpenAI or Anthropic):

1. Make a new key in the provider's console.
2. Put it in `.env` (edit the line; with v0.2.0 you can also run `init` again: see the note below).
3. Restart Antifaz so it reads `.env` again: `docker compose up -d --force-recreate`. (`docker compose restart` does **not** read `.env` again.)
4. Check it. With 0.1.1, there is no command that tests a provider key: send one small request from your client ([2.6](#26-point-your-clients-at-antifaz)). With v0.2.0: `docker compose exec antifaz antifaz doctor --providers`.
5. Revoke the old key in the provider's console.
6. Delete the `.env.bak-*` copies that hold the old key, if you have any.

**The Antifaz key** (`ANTIFAZ_API_KEY`):

1. Make a new random value (see [2.2](#22-write-the-env-file)) and put it in `.env`.
2. `docker compose up -d --force-recreate`. From now on the old key gets 401.
3. Give the new key to your clients: `ANTHROPIC_AUTH_TOKEN` for Claude Code (restart it), and the key of your SDK code.
4. Delete the `.env.bak-*` copies, if you have any.

Note: `init` **[v0.2.0]** always makes a new `ANTIFAZ_API_KEY` and asks for both provider keys again. Running it again changes all of them in one step.

## 8. Troubleshooting

With 0.1.1, your two tools are `docker compose logs antifaz` (why it does not start) and `docker compose exec antifaz antifaz verify` (that no data leaves). `antifaz doctor` arrives with v0.2.0.

| What you see | Why | What to do |
|---|---|---|
| `401` `{"error":{"code":"unauthorized",...}}` | The client did not send the Antifaz key, or sent another one (for example a real provider key). | Use `ANTIFAZ_API_KEY` from `.env` in `Authorization: Bearer <key>` or `x-api-key: <key>`, not both with different values. `Bearer` with capital B. In Claude Code: `ANTHROPIC_AUTH_TOKEN`, and open a new terminal after you set it. |
| `400` `Invalid host header` | The client used a host name that is not in `ANTIFAZ_ALLOWED_HOSTS`. | Add the name to `ANTIFAZ_ALLOWED_HOSTS` in `.env` (comma-separated), then `docker compose up -d --force-recreate`. |
| `403` `origin_not_allowed` | The request came from a web browser (it has an `Origin` header). Browsers are refused by default. | Call Antifaz from a server or a script. Only if you really need a browser, add its exact origin to `ANTIFAZ_ALLOWED_ORIGINS`. |
| `415` `unsupported_media_type` | The body is not sent as JSON. | Send `Content-Type: application/json`. |
| `400` `antifaz_blocked` | The request has an image, a PDF, a file or data in a place that cannot be masked. | See [section 6](#6-what-antifaz-blocks-images-pdfs-and-files). Send text only. |
| `503` `not_configured` | That provider has no key in `.env`. | Add its key and `docker compose up -d --force-recreate`. |
| Antifaz does not start; `docker compose ps` shows `restarting` | The configuration is refused, for example a weak key or an example value (`change-me...`). | `docker compose logs antifaz` shows the variable and the reason (never the value), for example `ANTIFAZ_API_KEY still has the example value from .env.example`. Fix `.env` and `docker compose up -d`. |
| Antifaz does not start with `ANTIFAZ_NER_ENABLED=true` | The published image does not include the NER (name detector) or its model. | Set `ANTIFAZ_NER_ENABLED=false`. Today the NER only runs from a clone (`make ner-model`, about 1.16 GB); see [TECNICO.md](TECNICO.md) (in Spanish). |
| Connection refused, or Claude Code says it cannot connect | Antifaz is not running, or the address or port is wrong. | `docker compose ps`, then `docker compose up -d`. Check the URL (`http://127.0.0.1:8000` by default). |
| `502` or `504` | The provider did not answer well or in time. | Check the provider's status and your network. With v0.2.0, `doctor --providers` tests the keys. |
| `docker compose exec antifaz antifaz doctor` says "invalid arguments" | `doctor` does not exist in 0.1.1. | Use `docker compose logs antifaz` and `docker compose exec antifaz antifaz verify`. `doctor` arrives with v0.2.0. |
| `init` says "no terminal to ask the questions" (v0.2.0) | `docker run` without `-it`. | Add `-it`, or use `--non-interactive`. |
| Linux: "Permission denied" when you read `.env` (v0.2.0) | `init` ran without `--user`, so the file belongs to the container user. | `sudo chown "$(id -u):$(id -g)" .env`, and next time use `--user "$(id -u):$(id -g)"`. |
| Windows: Antifaz does not start although `.env` looks right (with v0.2.0, `doctor` says "cannot read .env as UTF-8 text") | `.env` was saved in another encoding. In Windows PowerShell 5.1, `>` and `Out-File` write UTF-16 and `Set-Content` writes ANSI. | Open `.env` in Notepad, "Save as", encoding **UTF-8**. Do not create `.env` with `>`, `Out-File` or `Set-Content`. Use `Copy-Item .env.example .env` (or `init` with v0.2.0). |
| `docker compose exec antifaz sh` says "executable file not found" (v0.2.0) | From v0.2.0 the image is distroless: it has no shell, no `apt` and no `curl`. This is on purpose (fewer tools for an attacker). | `docker compose exec antifaz antifaz doctor` and `docker compose exec antifaz antifaz verify` still work. To look inside, see "Look inside the container" just below. |

### Look inside the container (v0.2.0, image without a shell)

The main way: a temporary container with tools (`busybox`, pinned by digest) **attached** to the Antifaz one. It shares its processes (`--pid`) and its network (`--network`), so you see its processes and reach `127.0.0.1:8000` as if you were inside. With `--rm` it is gone when you leave, and it does not change the Antifaz image or container.

**First, with no capability at all.** Enough to see the processes and ask the gateway:

```bash
c=$(docker compose ps -q antifaz)
docker run --rm -it --pid=container:$c --network=container:$c --cap-drop ALL --security-opt no-new-privileges --read-only busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e
# Inside:
ps                                   # process 1 is antifaz serve, as 10001
wget -qO- http://127.0.0.1:8000/healthz
```

Like this, `/proc/1/root` (the Antifaz files) and `/proc/1/environ` (its variables, with the keys) say "Permission denied". `--cap-drop ALL` also takes away the helper's ability to sniff the traffic of the network it shares (with Docker's default capabilities, a root helper could capture packets there).

**Only if you must look at files**, add `SYS_PTRACE` and nothing else:

```bash
docker run --rm -it --pid=container:$c --network=container:$c --cap-drop ALL --cap-add SYS_PTRACE --security-opt no-new-privileges --read-only busybox:1.37@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e
# Inside:
ls -a /proc/1/root/app               # the Antifaz files
```

In PowerShell: `$c = docker compose ps -q antifaz`, then the same line with `$c`. The Antifaz container still has no capabilities; the permission belongs to the helper only.

> **Careful with `SYS_PTRACE`.** It is the capability that lets a process read another one's environment (`/proc/1/environ`: **the keys**) and its **memory** (`/proc/1/mem`: the personal data of requests in flight and the marker table). In the variant above they stay closed only because the helper has no other capability (checked on 2026-10-16 with Docker Desktop 29.8: "Permission denied"); with Docker's default capabilities plus `SYS_PTRACE` both open (checked that day), and a helper run with `--user 10001` reads the environment even without capabilities. Use it only when needed, leave as soon as you are done, never take memory or file dumps, and if anything you saw was shared (screen, ticket, chat), rotate the keys.

Note: anyone with Docker access on the server can read the keys without any of this (for example `docker exec <container> python -c …` reads the environment, and `docker inspect` shows it). That is a limit of Docker, not of the image: see the [threat model](security/threat-model.md).

Alternative: `docker debug <container>`, which opens a shell with tools in any container, even one without a shell ([Docker documentation](https://docs.docker.com/reference/cli/docker/debug/)). It is a **Docker Desktop** feature, not part of the Docker Engine on a Linux server, and Docker says it is available with a **Pro, Team or Business** license ([Docker Desktop 4.33 announcement](https://www.docker.com/blog/docker-desktop-4-33/)); the reference page says nothing about plans, so check your license before you rely on it.

## 9. What is not tested yet

- **A real Claude Code session through Antifaz is pending.** `setup claude-code` and the Anthropic route are tested with the official Anthropic SDK and with recorded answers, but not yet with Claude Code against the real Anthropic API. It will be done when there is Anthropic credit. Until then, what this guide says about Claude Code comes from the tests and from the [Claude Code documentation](https://code.claude.com/docs/en/llm-gateway-connect).
- The OpenAI route was tested once against the real OpenAI API (2026-09-30).
- The steps of [section 3](#3-coming-in-v020-only-on-main-today) use the code on `main`. They are tested end to end with the image built from the repository, but v0.2.0 is not published yet.
