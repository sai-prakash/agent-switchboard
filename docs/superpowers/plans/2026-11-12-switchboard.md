# Agent Switchboard Implementation Plan

**Goal:** Provide a configurable, local Claude Code gateway with an honest provider verification UI.

**Architecture:** A local FastAPI service persists private connection data and routes Anthropic Messages calls through the LiteLLM SDK. A static web UI controls providers, verification, active route, and reversible Claude Code settings.

**Tech Stack:** Python 3.12, FastAPI, LiteLLM, vanilla browser JavaScript.

**Spec:** `docs/superpowers/specs/2026-11-12-switchboard-design.md`

1. Test private storage, provider validation, activation, and secret redaction; implement persistence and selection.
2. Test settings restoration and conflicts; implement reversible Claude Code connection.
3. Test gateway auth, non-streaming and streaming requests against an isolated local mock; implement forwarding through LiteLLM.
4. Build the provider editor, verification, activation, and setup UI. Verify service endpoints and browser interactions.
5. Document installation, model compatibility, credential storage, known limitations, and macOS login service setup.

Review focus: cross-origin requests, secret leakage, missing/disabled active routes, concurrent route changes, and user-modified Claude settings.
