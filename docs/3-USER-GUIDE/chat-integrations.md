# Chat Integrations

Ask your notebooks questions from **Telegram** or **WhatsApp** — same answers as the web chat, scoped to what your account may read.

---

## Linking your chat account

1. In Open Notebook, go to **Settings → Chat integrations**.
2. Click **Connect** on Telegram or WhatsApp — a 6-digit code appears, valid for 10 minutes.
3. In the chat app:
   - **Telegram**: open your bot's chat (the one whose token the admin configured) and send `/start <code>` — e.g. `/start 483920`.
   - **WhatsApp**: message the instance's Open Notebook number with `/start <code>`.
4. The page confirms automatically and shows the linked identity under **Manage**. You can unlink anytime from there — a lost phone stops working immediately.

One chat identity links to exactly one account, and answers respect your team's access scope — a member never sees another team's content, on any interface.

## Using the bot

The bot is conversational: it remembers your previous questions in the same chat, so follow-ups like *"and what did it say about pricing?"* just work.

**Commands** (everything else is treated as a question):

| Command | What it does |
|---|---|
| `/new` | Start a fresh conversation (the bot forgets the current topic) |
| `/search <query>` | Search without asking the AI — instant, formatted top-5 |
| `/unlink` | Disconnect this chat from your account |
| `/help` | List the commands |

**While it thinks:** the bot shows a typing indicator for the whole wait (answers can take 30–90 seconds). If it runs past ~45 seconds, it sends one "still working" message. Long answers arrive as multiple messages, and each answer ends with suggested follow-up questions you can tap out.

**If something fails:** the bot replies with what went wrong in plain language — a missing default model, a disabled account, a rate limit. The conversation itself survives a failed answer.

---

## WhatsApp (Baileys) risk warning

The WhatsApp adapter uses [Baileys](https://github.com/WhiskeySockets/Baileys), an **unofficial** WhatsApp Web library. Before enabling it (`OPEN_NOTEBOOK_WHATSAPP_ENABLED=true`), understand:

- **It violates WhatsApp's Terms of Service.** Meta can restrict or ban the number at its discretion. Telegram is the always-safe adapter.
- **Replies only.** The number must only answer people who message it first. Cold outreach is the documented ban trigger.
- **Never a verified business account.** Linking a blue-checkmark number is documented to cause instant, irreversible restriction.
- **Use a sacrificial number** — not anyone's primary — a real SIM, aged, on a residential IP, low volume.
- **Re-pairing happens.** If the phone's app reinstalls or the device is removed from *Linked devices*, the instance logs a loud re-pair alert; scan the fresh QR printed in the logs to re-pair.

---

## For admins

- The gateway runs as an always-on process (supervisord in Docker, `make gateway` locally) and idles with no platform connections until a token/env var is set. See the [environment reference](../5-CONFIGURATION/environment-reference.md#messenger-gateway-chat-integrations).
- Telegram: create a bot with [@BotFather](https://t.me/BotFather) and set `OPEN_NOTEBOOK_TELEGRAM_BOT_TOKEN`.
- Team access scoping is server-enforced for chat exactly as in the web UI; chat access ends the moment an account is disabled.
