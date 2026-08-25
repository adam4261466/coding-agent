# LinkedIn Conversation Assistant

This repository contains two separate systems:

- The original **Coding Agent** (`Start Agent.bat`) remains unchanged.
- The new **LinkedIn Conversation Assistant** is deliberately simple and never sends LinkedIn messages automatically.

## Workflow

1. Export your LinkedIn connections as a CSV with these columns:
   `First Name,Last Name,URL,Email Address,Company,Position,Connected On`
2. Put the file next to the scripts, or pass its path explicitly.
3. Run:
   `python import_connections.py --clean path\to\Connections.csv`
4. Start `Start LinkedIn Agent.bat`.
5. Search/select exactly one prospect.
6. Click **Open LinkedIn**. The saved URL is opened in your normal browser.
7. Click **Generate initial message**. Ollama uses that prospect's DB information and the recorded conversation.
8. Copy the draft into LinkedIn and send it yourself.
9. Click **Save as sent** and paste the exact message you actually sent.
10. When the prospect replies, paste the exact reply and click **Save prospect reply**.
11. Click **Generate reply** to create the next tailored response.
12. Repeat. Every real inbound/outbound message stays in `linkedin_agent.db`.

## Important separation

Do not use `agent.py`, `gui.py`, `run_agent.py`, or `Start Agent.bat` for LinkedIn outreach. Those belong to the general Coding Agent. The LinkedIn assistant uses only `linkedin_agent.py`, `linkedin_gui.py`, and `import_connections.py`.

## Ollama

The assistant defaults to:

- URL: `http://127.0.0.1:11434`
- Model: `gemma4:31b-cloud`

Override them with `OLLAMA_URL` and `OLLAMA_MODEL` environment variables.

The assistant has no LinkedIn sending tool. It stores only messages you explicitly record.
