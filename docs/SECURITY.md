# Security

QuotaDeck is not a password manager.

- Reuse already-logged-in CLI credentials. Never copy `auth.json` into the repo.
- Never write provider credential files. Token refresh belongs to the official CLI.
- Mask tokens, cookies, and Authorization headers in logs (keep last 4 characters).
- Read `state.vscdb` from a temporary copy, then delete the copy.
- `config.json` stores aliases and account-id hashes only.
- The LCD never shows a full email address by default.
