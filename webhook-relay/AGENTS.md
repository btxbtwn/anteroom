# Webhook relay: notes for agents

The root `AGENTS.md` has the rules and the working loop; `README.md` has the
endpoints and configuration.

- Tests: `cd webhook-relay && npm ci && npm test`. Signature unit tests run under
  `node --test`, then the Worker tests run in workerd through
  `@cloudflare/vitest-plugin`, with no Cloudflare account.
- Reproduce a bug as a Worker test first. Use `npx wrangler dev`
  (secrets in the gitignored `.dev.vars`) only when you need to send it real
  requests by hand.
- Deploy (`npx wrangler deploy`) only when the seller asks.
- Stored data stays as the root `AGENTS.md` describes: device IDs, token hashes,
  linked account IDs and event IDs, plus sign-in tokens for at most 15
  minutes. A change that stores anything else needs the seller's approval
  first.
