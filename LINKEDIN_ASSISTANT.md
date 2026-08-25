# LinkedIn Conversation Assistant

This repository contains only the simple LinkedIn conversation assistant.

## Files

- `connections.csv` — your private LinkedIn connections export, kept local and ignored by Git.
- `import_connections.py` — resets and imports `connections.csv` into the local database.
- `linkedin_agent.py` — prospect lookup, SQLite storage, browser opening, and Ollama message generation.
- `linkedin_gui.py` — the desktop interface.
- `Start LinkedIn Agent.bat` — imports `connections.csv` automatically and starts the GUI.

## CSV location

Place your LinkedIn export exactly here:

`connections.csv`

It must be beside the Python files and contain these columns:

`First Name,Last Name,URL,Email Address,Company,Position,Connected On`

The filename is fixed. You do not need to pass a path.

## Starting the assistant

Double-click:

`Start LinkedIn Agent.bat`

It will:

1. Check that `connections.csv` exists.
2. Delete/recreate the local `linkedin_agent.db`.
3. Import every valid connection from `connections.csv`.
4. Start the LinkedIn GUI.

## Conversation workflow

1. Search/select one prospect.
2. Click **Open LinkedIn** to open the saved URL in your normal browser.
3. Click **Generate initial message**.
4. Copy the generated draft into LinkedIn and send it manually.
5. Paste exactly what you sent and click **Save as sent**.
6. When the prospect replies, paste the exact reply and click **Save prospect reply**.
7. Click **Generate reply** to create the next personalized response.
8. Repeat for the conversation.

Nothing is sent automatically. The assistant only opens the saved profile URL, generates drafts with Ollama, and stores messages that you explicitly record.

## Database

The local database is:

`linkedin_agent.db`

It contains prospects, real inbound/outbound messages, and generated drafts. It is recreated from `connections.csv` every time `Start LinkedIn Agent.bat` is launched.

## Ollama

Defaults:

- URL: `http://127.0.0.1:11434`
- Model: `gemma4:31b-cloud`

You can override them with `OLLAMA_URL` and `OLLAMA_MODEL` environment variables.
