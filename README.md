# Freerider watch

Checks **Hertz Freerider** and **DriveBack** every 10 minutes and e-mails you when a
new free one-way car appears for:

- Stockholm (any station, incl. Bromma/Solna/Kista etc.) → Östersund
- Arlanda → Östersund
- Uppsala → Östersund

Each trip is e-mailed once (tracked in `seen.json`). Runs free on GitHub Actions.

## Setup (≈5 min)

1. **Gmail app password** – go to <https://myaccount.google.com/apppasswords>
   (requires 2-step verification), create one called "freerider", copy the 16 characters.
2. **Create a private GitHub repo** and push this folder to it (keep the
   `.github/workflows/watch.yml` path).
3. In the repo: **Settings → Secrets and variables → Actions → New repository secret**:
   | Name        | Value                          |
   |-------------|--------------------------------|
   | `SMTP_USER` | your Gmail address             |
   | `SMTP_PASS` | the app password from step 1   |
   | `MAIL_TO`   | where alerts go (optional)     |
4. **Actions tab → Freerider watch → Run workflow** once to test. Green run = working.
   From then on it runs every 10 min by itself.

## Notes

- GitHub's scheduler can lag 5–15 min at busy times, and pauses scheduled runs
  if the repo has no commits for 60 days (the bot's `seen.json` commits keep it alive
  whenever matches show up; otherwise just re-enable it in the Actions tab).
- Test locally without sending mail: `DRY_RUN=1 python watcher.py`
- Change routes: edit `ORIGINS` / `DESTINATION` at the top of `watcher.py`.
- Hertz links go to the front page (Hertz has no per-trip URL); DriveBack links go
  straight to the booking page.
- Not using Gmail? Set `SMTP_HOST` / `SMTP_PORT` as extra secrets/env vars.
