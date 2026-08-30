# Security and Render deployment

## Secrets

- Never store a Telegram token in source code, commits, screenshots, chat messages, or logs.
- Store `BOT_TOKEN` and `ADMIN_ID` only in Render Environment.
- If a token is exposed, revoke it in BotFather immediately and generate a replacement.
- A removed secret remains visible in Git history; revocation is mandatory.

## Render settings

Use a single Render **Web Service** instance with:

- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- Health check path: `/`
- Auto-deploy: enabled only for the protected production branch

Environment variables:

- `BOT_TOKEN`: current BotFather token
- `ADMIN_ID`: numeric Telegram ID of the administrator
- `DATA_DIR`: `/var/data`

Attach a persistent disk mounted at `/var/data`. Without a disk, user progress will be lost on deploy or restart.

## Access controls

- Keep the GitHub repository private.
- Require two-factor authentication on GitHub, Render, and the Telegram account that owns the bot.
- Review GitHub collaborators, deploy keys, webhooks, Render team members, and environment access.
- Protect the production branch and merge changes through pull requests.
- Do not expose Render environment values in screenshots.

## Incident recovery

1. Revoke the exposed token in BotFather.
2. Review BotFather settings and remove unknown administrators or integrations.
3. Create a new token and put it only in Render Environment.
4. Deploy the hardened branch.
5. Check Render logs for exactly one polling process and no authorization or conflict errors.
6. Test `/start`, `/help`, one answer, restart the service, and confirm progress persists.
7. Rotate any other secret ever committed to the repository.
