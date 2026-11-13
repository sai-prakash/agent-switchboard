# Model Switchboard

A local-first LLM gateway for one user on one machine. Point Claude Code, Cline, Continue, Aider, OpenCode, Codex, or any OpenAI-compatible client at a loopback listener, then route each request through a provider you already pay for: Anthropic, Grok, GitHub Copilot, ChatGPT, Ollama, or a generic API.

The dashboard title and data directory still use the name **Agent Switchboard**. This repository is `model-switchboard`.

```
Claude Code / Cline / Continue / Aider
        │  Anthropic Messages or OpenAI Chat Completions
        ▼
127.0.0.1:18765  Model Switchboard
        │  smart route or pinned provider
        ▼
Anthropic API · Grok CLI · Copilot · ChatGPT · Ollama · OpenRouter · …
```

The system proxy is never changed. The listener binds only to `127.0.0.1`.

## Features

- **One local URL** for Anthropic Messages (`http://127.0.0.1:18765`) and OpenAI Chat Completions (`http://127.0.0.1:18765/v1`)
- **Connect Claude Code** from the browser. Disconnect restores the previous user settings and refuses if those keys were edited elsewhere
- **Access scan** that treats a catalog list as a hint, not as access. Copilot Claude ids that return `model_not_supported` are marked blocked
- **Smart routing** that prefers a proven Claude-family route, then Grok or GPT, and fails over on quota, expiry, or `model_not_supported`
- **Official Anthropic** as a first-class provider, always calling `https://api.anthropic.com`, never this listener
- **ChatGPT and Copilot** device logins stored in Switchboard’s own account folder, not reused from Codex or the Copilot CLI
- **Grok Build** via the existing `grok login` session
- **Ollama and generic OpenAI-compatible** endpoints
- Portable data dir: `AGENT_SWITCHBOARD_HOME`, else macOS Application Support, Windows `%APPDATA%`, Linux XDG

## Requirements

- Python 3.12+
- macOS, Linux, or Windows
- Optional: Claude Code, `grok` CLI, GitHub Copilot plan, ChatGPT plan, Ollama

## Install

```sh
git clone https://github.com/sai-prakash/model-switchboard.git
cd model-switchboard
python3 -m venv .venv
```

macOS / Linux:

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/python service.py start
```

Windows:

```bat
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe service.py start
```

Open [http://127.0.0.1:18765](http://127.0.0.1:18765).

On macOS, `service.py start` kickstarts the login LaunchAgent if one is already installed. Otherwise it runs Uvicorn in the foreground.

```sh
.venv/bin/python service.py install     # macOS login item
.venv/bin/python service.py uninstall   # remove the login item only
```

Uninstall does not delete provider data or restore Claude Code settings. Disconnect in the dashboard first. The installed service depends on this checkout staying at its current path.

## Use it

1. Add a provider (API key, device login, or local endpoint).
2. Select **Test**, then **Use**.
3. Select **Scan access** so routing knows which saved models this account can actually call.
4. Select **Connect Claude Code** and restart Claude Code.
5. For other tools, copy the OpenAI URL and gateway token from the dashboard.

| Client | Base URL | Auth | Model |
| --- | --- | --- | --- |
| Claude Code, Goose (Anthropic) | `http://127.0.0.1:18765` | `x-api-key` or `ANTHROPIC_AUTH_TOKEN` | `switchboard` |
| Cline, Continue, Aider, Open WebUI | `http://127.0.0.1:18765/v1` | `Authorization: Bearer` | `switchboard` |
| OpenAI SDK | `http://127.0.0.1:18765/v1` | API key = gateway token | `switchboard` |

`GET /v1/models` and `POST /v1/chat/completions` are implemented. The requested model id is currently ignored; Switchboard routes to the active provider or the smart-routed candidate.

Config lives in `AGENT_SWITCHBOARD_HOME` if set, otherwise:

| OS | Directory |
| --- | --- |
| macOS | `~/Library/Application Support/Agent Switchboard` |
| Windows | `%APPDATA%\Agent Switchboard` |
| Linux | `~/.config/agent-switchboard` |

That directory is **not** part of this git repo. It holds the gateway token, provider keys, and device-login stores with owner-only permissions.

## Providers

**Anthropic Claude.** Add the Anthropic API preset and paste a key from [console.anthropic.com](https://console.anthropic.com). Use `anthropic/claude-sonnet-4-6`, `anthropic/claude-opus-4-6`, or another Claude API model id. Switchboard always calls `https://api.anthropic.com` and refuses a base URL that points at this gateway. It does not read process-env `ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL` for upstream calls, because Claude Code uses those to reach Switchboard. Optional override: `SWITCHBOARD_ANTHROPIC_API_KEY`.

**ChatGPT / Codex.** Connect from the dashboard with a device code. Switchboard stores its own login under `accounts/chatgpt` in the data directory and does not reuse the Codex CLI session. Use a `chatgpt/<model>` identifier such as `chatgpt/gpt-6.1-sol`.

**GitHub Copilot.** Connect from the dashboard with a GitHub device code. Use a `github_copilot/<model>` identifier such as `github_copilot/gpt-4o`. Claude models appear in Copilot’s catalog on some plans and return `model_not_supported` on others. Scan access before relying on a Copilot Claude id.

**Grok Build.** Uses the existing `grok login` session in `~/.grok/auth.json` against the CLI chat proxy (`GROK_CLI_CHAT_PROXY_BASE_URL`, default `https://cli-chat-proxy.grok.com/v1`). This is not the billed xAI console API. Use a `grok/<model>` identifier from the CLI model cache, for example `grok/grok-4.6`. Session tokens expire; run `grok login` again if requests fail. Do not paste `XAI_API_KEY` for this route.

**Ollama and generic APIs.** LiteLLM identifiers such as `ollama_chat/<model>`, `openai/<model>`, `gemini/<model>`, `xai/<model>`, or `openrouter/<provider>/<model>` work. The generic OpenAI-compatible preset uses Chat Completions, so a Responses-only model may not work.

**Google Antigravity.** Optional unofficial adapter around a local `agy` Keychain login. Google’s [Antigravity FAQ](https://antigravity.google/docs/faq/) states that using third-party software, including Claude Code, with an Antigravity login can violate the terms of service. Prefer a Gemini API key from [Google AI Studio](https://aistudio.google.com/) instead. The adapter is unofficial and can break when Google or `agy` changes. Streaming is buffered so a failed stream cannot look like a successful reply.

**Test** makes a small, billable non-streaming request and only confirms that basic request. **Models** lists known subscription models or calls `GET /models` on a saved OpenAI-compatible base URL.

Gemini CLI and OpenCode are detected when installed. They do not yet have a Switchboard subscription adapter; configure an API or an OpenAI-compatible endpoint you are allowed to use.

## Smart routing

Access scan sends a tiny request to each enabled provider and records `ready`, `quota`, `unsupported`, `auth`, or `rate_limited`, plus last usage. With **Smart routing** on, new requests use the best proven route and skip listed-but-blocked ids. Catalog lists are not treated as access.

A 429 or quota on the active route can fail over to the next candidate (max 3). Switching applies to new requests. Start a new Claude Code session if the conversation is incompatible with the new provider.

Anthropic does not support routing Claude Code to non-Claude models. Translated OpenAI Chat Completions may drop provider-specific features. Optional endpoints such as token counting, embeddings, and the OpenAI Responses API are not implemented. There is no cost cap.

## Privacy and safety

- Binds only to `127.0.0.1:18765`. Do not expose it on a LAN or the public internet.
- Browser controls require a same-origin request plus `x-switchboard-ui: 1`.
- Inference endpoints require the random gateway token (`x-api-key` or `Authorization: Bearer`).
- Provider keys and subscription tokens are stored **unencrypted** in the data directory with owner-only permissions. They are not shown again in the form.
- The gateway token is shown in the local dashboard so other tools can copy it. It is also written into Claude Code user settings when you connect.
- Grok reuses `~/.grok/auth.json` and does not copy that session into Switchboard config.
- Prompt and response bodies are not deliberately logged. Upstream errors may still contain diagnostic text.
- Other processes running as your user can read these files.
- Do not commit `config.json`, `.env`, Keychain dumps, or `~/.grok/auth.json`. This repository’s `.gitignore` blocks the usual paths.

Using Copilot, ChatGPT, Grok CLI, or Antigravity as a generic proxy may be outside those products’ terms. Billed Anthropic / OpenAI / Gemini / xAI keys and local Ollama are the clean backends.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use a temporary data directory and a simulated OpenAI-compatible upstream. They do not call live provider accounts.

Vendored Antigravity proxy (MIT, © 2026 Usama Shehab) lives in `vendor-research/antigravity-proxy/` and is loaded only for `antigravity/` models.

## What this repository does not contain

No API keys, OAuth tokens, gateway tokens, Claude Code settings, Grok sessions, Copilot/ChatGPT device logins, or `config.json`. Those stay on the machine that runs the gateway.

## License

MIT. See [LICENSE](LICENSE).
