# Install guide

[Leer en español](instalacion.md)

This guide is for a company that wants to run Antifaz on its own machine or server, and point its tools (OpenAI or Anthropic SDKs, curl, Claude Code) at it. Every command here comes from the code of the CLI and was checked against its `--help`.

Remember: Antifaz pseudonymises. It only protects the personal data it detects. Test it with your own kind of texts before you use it with real data.

> [!IMPORTANT]
> **Some steps need v0.2.0, which is not released yet.** They are marked **[v0.2.0]**. Today (v0.1.0) you can follow the path marked **[v0.1.0]**: it uses the files of the repository instead of the release files.

> [!NOTE]
> Screenshots will be added after the design prototype (#30).

## Contents

1. [Requirements](#1-requirements)
2. [Step by step](#2-step-by-step)
3. [If Antifaz is down, and how to switch it off](#3-if-antifaz-is-down-and-how-to-switch-it-off)
4. [Claude Code traffic that does not go through Antifaz](#4-claude-code-traffic-that-does-not-go-through-antifaz)
5. [What Antifaz blocks: images, PDFs and files](#5-what-antifaz-blocks-images-pdfs-and-files)
6. [Upgrade, clean backups and rotate keys](#6-upgrade-clean-backups-and-rotate-keys)
7. [Troubleshooting](#7-troubleshooting)
8. [What is not tested yet](#8-what-is-not-tested-yet)

## 1. Requirements

To run the gateway:

- **Docker with Compose.** Docker Desktop on Windows or macOS, or Docker Engine with the Compose plugin on Linux. Check it with `docker compose version`.
- The image is only tested on `linux/amd64` (Intel or AMD processors). It is not tested on ARM (for example Apple Silicon or Raspberry Pi).
- A key of at least one provider: OpenAI, Anthropic, or both.

To run the CLI on your computer (only for `setup claude-code`, and optional for `doctor` and `verify`):

- **Python 3.12** and **[uv](https://docs.astral.sh/uv/)**, and a clone of the repository:

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
uv sync
```

`antifaz setup claude-code` and `antifaz doctor` are on the `main` branch, not in v0.1.0. Until v0.2.0 is out, use a clone of `main`.

## 2. Step by step

Antifaz listens only on `127.0.0.1` (this machine). Other machines cannot reach it. To use it from other machines, see [Use it from other machines](#use-it-from-other-machines).

### 2.1 Get the Compose file

**[v0.2.0]** Each release has a `docker-compose.yml` file that pins the image by digest (the exact image that was built, tested and signed). In an empty folder:

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

**[v0.1.0]** There is no Compose file in the v0.1.0 release. Use the one in the repository: it starts the published image `ghcr.io/miquel-moreno/antifaz:0.1.0`.

```bash
git clone https://github.com/miquel-moreno/antifaz
cd antifaz
```

### 2.2 Write the `.env` file

The `.env` file holds the keys. Antifaz reads it when the container starts. Never commit it to git and never send it by email or chat.

**[v0.2.0] With `antifaz init`** (in the container, no clone needed). It asks for your provider keys without showing them, writes `.env`, and makes a new random `ANTIFAZ_API_KEY`. It shows that key **once**: copy it, your clients use it. No key goes on the command line.

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
- `init` also always writes `ANTIFAZ_ADMIN_TOKEN`, a second random value for the browser panel at `http://localhost:8000/panel` (arriving in v0.2). It is shown once, like the key, and it is **not** the key your clients use. **To turn the panel off**, delete the `ANTIFAZ_ADMIN_TOKEN` line from `.env` and restart (`docker compose up -d --force-recreate`).

> In Git Bash on Windows, use PowerShell for this command: Git Bash changes paths like `/work` and the command fails.

**[v0.1.0] By hand.** In the folder of the clone:

```bash
cp .env.example .env
```

```powershell
Copy-Item .env.example .env
```

Then open `.env` in a text editor and change:

- `ANTIFAZ_API_KEY`: a random value of at least 32 characters. To make one:
  - bash: `openssl rand -hex 32`
  - PowerShell: `$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') })`
- `ANTIFAZ_OPENAI_API_KEY` and/or `ANTIFAZ_ANTHROPIC_API_KEY`: your provider keys. Delete the line of a provider you do not use (its routes answer 503).
- `ANTIFAZ_ADMIN_TOKEN`: the token of the browser panel (arriving in v0.2). Another random value, made the same way and different from `ANTIFAZ_API_KEY`. If you do not want the panel, delete the line.
- `ANTIFAZ_TRUSTED_PROXIES`: leave it empty unless Antifaz is behind a reverse proxy (see [Use it from other machines](#use-it-from-other-machines)).

Rules for the values: no spaces, no quotes and no `$` (Compose would try to replace it). On Windows, save the file as **UTF-8** (see [Troubleshooting](#7-troubleshooting)).

Antifaz refuses to start while a key still has the example value (`change-me...`).

### 2.3 Start Antifaz

In the folder with `docker-compose.yml` and `.env` (same command in bash and PowerShell):

```bash
docker compose up -d
```

Check it:

```bash
docker compose ps                    # the "antifaz" service should be "healthy" after a few seconds
curl http://127.0.0.1:8000/healthz   # {"status":"ok","version":"..."}
```

In PowerShell, use `curl.exe` instead of `curl`.

If the service shows `restarting`, Antifaz refused the configuration. Read why with `docker compose logs antifaz`: it names the variable to fix, never its value.

### 2.4 Check it with `doctor`

**[v0.2.0]** `doctor` checks the configuration and the running gateway. It names the setting to fix, never its value, and lists the next steps.

```bash
docker compose exec antifaz antifaz doctor
```

To also test your provider keys, add `--providers`. It only asks each provider for its list of models, which is free:

```bash
docker compose exec antifaz antifaz doctor --providers
```

Inside the container `doctor` says "no .env in that folder: only environment variables were read". That is normal: in the container the settings come from the environment, the same ones the gateway uses.

Exit code: 0 if every check passed, 1 if something failed.

### 2.5 Prove that no data leaves with `verify`

`verify` uses **your** configuration, plants fake personal data (DNI, NIE, IBAN, email, phone, card) in every part of a request, and fails if one value reaches a fake provider. It never calls a real provider and costs nothing.

```bash
docker compose exec antifaz antifaz verify
```

This works with v0.1.0 too. From a clone, `uv run antifaz verify` does the same with the `.env` of the current folder.

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

**Claude Code.** On the computer where you use Claude Code, from a clone (needs **[v0.2.0]** or a clone of `main`):

```bash
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
- Restart Claude Code. `/status` should show the line "Anthropic base URL" with Antifaz's address.
- If Antifaz runs on another address, add `--url`, for example `--url https://antifaz.example.internal`.
- It configures the Claude Code CLI. The VS Code extension and the desktop app read their own settings ([Claude Code docs](https://code.claude.com/docs/en/llm-gateway-connect)); this command does not change them.

### Use it from other machines

Antifaz speaks plain HTTP and listens only on `127.0.0.1`. To use it from other machines:

1. Put a reverse proxy with HTTPS in front (Caddy, nginx, Traefik). Do not publish the port on `0.0.0.0`.
2. Add the name that clients use to `ANTIFAZ_ALLOWED_HOSTS` in `.env` (for example `antifaz.example.internal`), then run `docker compose up -d --force-recreate`.
3. The proxy must not save request bodies in its logs: they hold the personal data before it is masked.
4. Only for the panel (v0.2): put the **exact IP address of the proxy**, as Antifaz sees it, in `ANTIFAZ_TRUSTED_PROXIES` (one address, for example `172.20.0.10`). In Compose, give the proxy container a fixed address (`ipv4_address` under its network) so it does not change. Only then does the panel believe the proxy's `X-Forwarded-For` and `X-Forwarded-Proto`.
   - **Never** put the Docker gateway (`172.x.0.1`) or the whole Docker network: requests that come in through the published port arrive from the gateway, so any client could then fake those headers. Never `0.0.0.0/0`. `antifaz doctor` warns about ranges wider than /24.
   - The proxy must add `X-Forwarded-For`. If it does not, every client looks like the proxy and they all share one login limit (5 failures a minute).
   - Without a proxy, leave it empty.

More details in [TECNICO.md](TECNICO.md#proxy-inverso-https) (in Spanish).

## 3. If Antifaz is down, and how to switch it off

**If Antifaz is down**, your client gets a connection error and **nothing is sent** to the provider. The client does not go to the provider directly by itself. In Claude Code you see an error like "Connection refused" or "Unable to connect to API".

**To switch it off in a minute** (go back to the provider directly):

1. Claude Code: remove the setting, from the clone:

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

## 4. Claude Code traffic that does not go through Antifaz

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

## 5. What Antifaz blocks: images, PDFs and files

Antifaz cannot look for personal data inside an image, a PDF or a file. So it **blocks** any request that carries one, instead of sending it unchecked ("fail-closed", [ADR-0006](adr/0006-campos-desconocidos.md), in Spanish). The client gets:

```json
{"error": {"code": "antifaz_blocked", "message": "attachments are not supported; request blocked"}}
```

with HTTP status 400. The same happens with file ids, documents, citations and Anthropic server tools (like web search).

What this means for Claude Code users:

- **Pasting a screenshot** or asking Claude Code to read an image or a PDF makes that request fail with this error. Nothing is sent to the provider.
- The image stays in the conversation, so the next messages will probably fail too. Start a new conversation with `/clear`.
- Copy the text you need as text instead of a screenshot.

This behaviour comes from the gateway tests. It is not checked with a real Claude Code session yet (see [section 8](#8-what-is-not-tested-yet)).

## 6. Upgrade, clean backups and rotate keys

### Upgrade

Read the [CHANGELOG](../CHANGELOG.md) of the new version first: a "Breaking" line means you must change something.

**[v0.2.0 and later]** With the release Compose file, in its folder (change `vX.Y.Z` to the new version):

```bash
curl -LO https://github.com/miquel-moreno/antifaz/releases/download/vX.Y.Z/docker-compose.yml
docker compose pull
docker compose up -d
docker compose exec antifaz antifaz doctor
```

In PowerShell, `curl.exe` instead of `curl`. Your `.env` stays as it is. To go back, download the Compose file of the old version and run the same commands.

**[v0.1.0]** With the clone: `git pull`, then `docker compose pull` and `docker compose up -d`. The Compose file of the repository uses the last published version. To choose one, set `ANTIFAZ_VERSION` (for example `ANTIFAZ_VERSION=0.1.0 docker compose up -d`).

From 0.1.0 to 0.2.0 the image starts with `antifaz serve` instead of `uvicorn ...`. The Compose file and the `docker run` command of the README keep working. A `docker run IMAGE <uvicorn options>` no longer works.

### Clean backups

Antifaz keeps a copy every time it replaces a file. **The copies hold your old keys or settings.** Delete them when you do not need them:

- `.env.bak-YYYYMMDD-HHMMSS`, next to `.env` (made by `init`):
  - bash: `rm .env.bak-*`
  - PowerShell: `Remove-Item .env.bak-*`
- `settings.json.bak-YYYYMMDD-HHMMSS`, next to `~/.claude/settings.json` (made by `setup claude-code`).
- With `--project`: `~/.claude/antifaz-backups/` (on Windows, `%USERPROFILE%\.claude\antifaz-backups\`).

`setup claude-code` prints the full path of each copy when it writes it.

### Rotate keys

**A provider key** (OpenAI or Anthropic):

1. Make a new key in the provider's console.
2. Put it in `.env` (edit the line, or run `init` again: see the note below).
3. Restart Antifaz so it reads `.env` again: `docker compose up -d --force-recreate`. (`docker compose restart` does **not** read `.env` again.)
4. Check it: `docker compose exec antifaz antifaz doctor --providers` **[v0.2.0]**.
5. Revoke the old key in the provider's console.
6. Delete the `.env.bak-*` copies that hold the old key.

**The Antifaz key** (`ANTIFAZ_API_KEY`):

1. Make a new random value (see [2.2](#22-write-the-env-file)) and put it in `.env`.
2. `docker compose up -d --force-recreate`. From now on the old key gets 401.
3. Give the new key to your clients: `ANTHROPIC_AUTH_TOKEN` for Claude Code (restart it), and the key of your SDK code.
4. Delete the `.env.bak-*` copies.

Note: `init` **[v0.2.0]** always makes a new `ANTIFAZ_API_KEY` and asks for both provider keys again. Running it again changes all of them in one step.

## 7. Troubleshooting

| What you see | Why | What to do |
|---|---|---|
| `401` `{"error":{"code":"unauthorized",...}}` | The client did not send the Antifaz key, or sent another one (for example a real provider key). | Use `ANTIFAZ_API_KEY` from `.env` in `Authorization: Bearer <key>` or `x-api-key: <key>`, not both with different values. `Bearer` with capital B. In Claude Code: `ANTHROPIC_AUTH_TOKEN`, and open a new terminal after you set it. |
| `400` `Invalid host header` | The client used a host name that is not in `ANTIFAZ_ALLOWED_HOSTS`. | Add the name to `ANTIFAZ_ALLOWED_HOSTS` in `.env` (comma-separated), then `docker compose up -d --force-recreate`. |
| `403` `origin_not_allowed` | The request came from a web browser (it has an `Origin` header). Browsers are refused by default. | Call Antifaz from a server or a script. Only if you really need a browser, add its exact origin to `ANTIFAZ_ALLOWED_ORIGINS`. |
| `415` `unsupported_media_type` | The body is not sent as JSON. | Send `Content-Type: application/json`. |
| `400` `antifaz_blocked` | The request has an image, a PDF, a file or data in a place that cannot be masked. | See [section 5](#5-what-antifaz-blocks-images-pdfs-and-files). Send text only. |
| `503` `not_configured` | That provider has no key in `.env`. | Add its key and `docker compose up -d --force-recreate`. |
| Antifaz does not start; `docker compose ps` shows `restarting` | The configuration is refused, for example a weak key or an example value (`change-me...`). | `docker compose logs antifaz` shows the variable and the reason (never the value), for example `ANTIFAZ_API_KEY still has the example value from .env.example`. Fix `.env` and `docker compose up -d`. |
| Antifaz does not start with `ANTIFAZ_NER_ENABLED=true` | The published image does not include the NER (name detector) or its model. | Set `ANTIFAZ_NER_ENABLED=false`. Today the NER only runs from a clone (`make ner-model`, about 1.16 GB); see [TECNICO.md](TECNICO.md) (in Spanish). |
| Connection refused, or Claude Code says it cannot connect | Antifaz is not running, or the address or port is wrong. | `docker compose ps`, then `docker compose up -d`. Check the URL (`http://127.0.0.1:8000` by default). |
| `502` or `504` | The provider did not answer well or in time. | Check the provider's status and your network. `doctor --providers` **[v0.2.0]** tests the keys. |
| `init` says "no terminal to ask the questions" | `docker run` without `-it`. | Add `-it`, or use `--non-interactive`. |
| Linux: "Permission denied" when you read `.env` | `init` ran without `--user`, so the file belongs to the container user. | `sudo chown "$(id -u):$(id -g)" .env`, and next time use `--user "$(id -u):$(id -g)"`. |
| Windows: `doctor` says "cannot read .env as UTF-8 text", or Antifaz does not start although `.env` looks right | `.env` was saved in another encoding. In Windows PowerShell 5.1, `>` and `Out-File` write UTF-16 and `Set-Content` writes ANSI. | Open `.env` in Notepad, "Save as", encoding **UTF-8**. Do not create `.env` with `>`, `Out-File` or `Set-Content`. Use `init` or `Copy-Item .env.example .env`. |

## 8. What is not tested yet

- **A real Claude Code session through Antifaz is pending.** `setup claude-code` and the Anthropic route are tested with the official Anthropic SDK and with recorded answers, but not yet with Claude Code against the real Anthropic API. It will be done when there is Anthropic credit. Until then, what this guide says about Claude Code comes from the tests and from the [Claude Code documentation](https://code.claude.com/docs/en/llm-gateway-connect).
- The OpenAI route was tested once against the real OpenAI API (2026-09-30).
- The **[v0.2.0]** steps use the code on `main`. They are tested end to end with the image built from the repository, but v0.2.0 is not published yet.
