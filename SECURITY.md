# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security problems.** Public issues are
visible to everyone and could expose users before a fix is available.

Instead, report it privately:

- Preferred: GitHub's **"Report a vulnerability"** button under the repo's
  **Security** tab (Security Advisories), or
- Contact the maintainer directly through the channels listed on the maintainer's
  GitHub profile.

This is a solo-maintained hobby project, so responses are best-effort – but
genuine security reports are taken seriously and I will try to acknowledge them
promptly.

## Sensitive surfaces

charts-stack handles several credentials and secrets. Keep them out of issues,
logs and screenshots:

- Web-UI login passwords (`DASHBOARD_PASSWORD`, `CONFIG_PASSWORD`,
  `SONGREQUEST_USERS`) and the `SESSION_SECRET`.
- Soulseek / slskd credentials (`SLSKD_API_KEY` and the Soulseek login).
- Pushover token and user key (`PUSHOVER_TOKEN`, `PUSHOVER_USER`).
- Last.fm, Lidarr and ListenBrainz API keys/tokens.

All of these live in your local `.env`, which is gitignored and never committed.

## Never paste real secrets

When filing a bug report or sharing logs, **always redact real tokens,
passwords, API keys and session secrets first.** A placeholder like `***` is
enough for debugging.
