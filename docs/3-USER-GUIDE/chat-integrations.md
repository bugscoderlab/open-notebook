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

The WhatsApp adapter uses [Baileys](https://github.com/WhiskeySockets/Baileys), an **unofficial** WhatsApp Web library. It is **enabled by default** (`OPEN_NOTEBOOK_WHATSAPP_ENABLED=false` to disable). Before using it, understand:

- **It violates WhatsApp's Terms of Service.** Meta can restrict or ban the number at its discretion. Telegram is the always-safe adapter.
- **Replies only.** The number must only answer people who message it first. Cold outreach is the documented ban trigger.
- **Never a verified business account.** Linking a blue-checkmark number is documented to cause instant, irreversible restriction.
- **Use a sacrificial number** — not anyone's primary — a real SIM, aged, on a residential IP, low volume.
- **Re-pairing happens.** If the phone's app reinstalls or the device is removed from *Linked devices*, the instance logs a loud re-pair alert; pair again with the fresh QR in the web UI (Settings → Chat integrations → Connect WhatsApp). An admin can also force it: **"Re-pair with a different number"** in the Connect dialog tells the gateway to wipe its session and mint a fresh QR — no server access needed.

---

## For admins

- The gateway runs as an always-on process (supervisord in Docker, `make gateway` locally). Telegram connects only when a bot token is set; WhatsApp is on by default (`OPEN_NOTEBOOK_WHATSAPP_ENABLED=false` to disable). See the [environment reference](../5-CONFIGURATION/environment-reference.md#messenger-gateway-chat-integrations).
- Telegram: create a bot with [@BotFather](https://t.me/BotFather) and set `OPEN_NOTEBOOK_TELEGRAM_BOT_TOKEN`.
- Team access scoping is server-enforced for chat exactly as in the web UI; chat access ends the moment an account is disabled.

### Manual smoke test (Telegram, against a dev instance)

1. Start the stack (`make start-all` or the Herdr dev script) — the gateway logs `internal token accepted` and `telegram adapter connected`.
2. In the web UI: Settings → Chat integrations → Connect Telegram — note the 6-digit code.
3. In Telegram, send your bot `/start <code>` — it replies `Linked as <your email>`.
4. Send any question — expect a typing indicator, then a scoped answer quoting your message, then suggested follow-ups.
5. Send `/search <something>` — expect a numbered top-5 list. Send `/new`, `/help`, `/unlink` — each behaves per the table above.

If step 3 says the code expired, generate a fresh one in the UI (codes live 10 minutes).

### Manual smoke test (WhatsApp, against a dev instance)

1. Start the stack — the gateway logs `internal token accepted` and begins WhatsApp pairing.
2. In the web UI: Settings → Chat integrations → Connect WhatsApp — a **pairing QR** appears (the gateway pushes it to the API; it rotates automatically), alongside your 6-digit linking code.
3. On the phone: WhatsApp → Settings → Linked devices → Link a device → scan the QR. Once the gateway logs `whatsapp adapter connected`, the dialog switches to "WhatsApp is connected".
4. Link the account, either way:
   - **One-click** (sole-number setups, where the bot runs on your own number): click **"This is my number — link it to my account"** in the dialog. No chat message needed — WhatsApp doesn't reliably relay self-chat messages to linked devices.
   - **Chat flow**: from *your own* WhatsApp, send the bot number `/start <code>` — it replies `Linked as <your email>`.
5. Send any question — expect a scoped answer quoting your message, then suggested follow-ups.

The QR also still renders in the gateway terminal (developer convenience) — the web UI is the operator path.
