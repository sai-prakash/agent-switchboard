# Agent Switchboard design

An explicitly configured, loopback-only Claude Code gateway for one Mac. The web interface manages connections, validates them with a real request, selects the active route, and connects or disconnects Claude Code. Existing sessions may need restarting. The service never reads coding-agent login stores or impersonates their clients.

Claude Code sends Anthropic Messages requests to this service. LiteLLM translates the Messages protocol to supported provider APIs, including streaming and tool calls. Each request captures the currently selected provider; switching applies to new requests. Provider compatibility is not guaranteed and verification results describe only the tested request. Anthropic does not support non-Claude models in Claude Code.

Provider API credentials and a random local gateway token are stored in a user-only local file. Management endpoints require a same-origin browser request; the gateway checks its local token. Do not expose the listener to the network. Prompt and response contents are not recorded. API use may incur charges.

Claude Code connection is opt-in in the UI and modifies only the relevant keys in its user settings. Disconnect restores their prior values, refusing to overwrite changes made by another application. The app does not alter the macOS system proxy.

Visual direction: a compact network control room with a pale cool-gray canvas, ink text, cobalt routing accents, and green health indicators. The selected route forms a horizontal signal path; the provider list is a functional table; the editor is a focused side panel. System sans typography keeps a native Mac feel.
