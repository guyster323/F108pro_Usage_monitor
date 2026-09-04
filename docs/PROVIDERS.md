# Providers

QuotaDeck never writes provider credential files. Tokens stay with the official CLI.

| Provider | Source | Credential path | Usage API |
| --- | --- | --- | --- |
| Codex | CLI | `$CODEX_HOME/auth.json`, `~/.codex`, extra homes | `GET chatgpt.com/backend-api/wham/usage`, fallback `codex app-server` |
| Cursor | App | `%APPDATA%\Cursor\User\globalStorage\state.vscdb` | `GET cursor.com/api/usage-summary`, fallback api2 RPC |
| Cursor | CLI | `%APPDATA%\Cursor\auth.json` | same |
| Claude | CLI | `~/.claude/.credentials.json` | `GET api.anthropic.com/api/oauth/usage` |
| Grok | CLI | `~/.grok/auth.json` | `GET cli-chat-proxy.grok.com/v1/billing?format=credits` |

Cursor HUD windows follow the dashboard bars, not included-spend cents:

- `AUTO` ← `individualUsage.plan.autoPercentUsed` (Cursor Models: Auto / Composer / Cursor Grok)
- `OTHER` ← `individualUsage.plan.apiPercentUsed` (Other Models)

`plan.used / plan.limit` is a cents bucket and does not match those percentages.

The settings app lists each signed-in source and lets you enable only the accounts that should rotate on the F108 Pro. The same Cursor user found in both App and CLI is shown once (App first).

Claude and Grok were implemented from documented APIs and fixtures. Live verification
depends on a logged-in CLI on the user's machine.

Normalized snapshot fields: `provider`, `account_id`, `display_name`, `plan`,
`windows[]` (`id`, `label`, `used_percent`, `remaining_percent`, `resets_at`),
`status`, `fetched_at`.
