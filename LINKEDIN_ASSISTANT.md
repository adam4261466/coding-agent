# LinkedIn Conversation CRM

This branch contains only the simple LinkedIn conversation assistant.

## Prospect sources

There are two ways to add a person:

### 1. Existing LinkedIn connections
Keep your LinkedIn export beside the scripts as `connections.csv`. The importer understands LinkedIn's `Notes:` preamble and finds the real CSV header automatically.

### 2. Any LinkedIn profile URL
Use **Add prospect by URL** in the GUI and paste a full LinkedIn profile URL. The assistant opens that profile in its dedicated Chrome profile, reads text visible on the page, stores the captured profile context, marks the person as **Not connected**, and then lets you generate a personalized first-contact draft.

The assistant does not send connection requests or messages automatically.

## Pipeline

Every prospect is classified from the real conversation stored in `linkedin_agent.db`:

- **Not contacted** — no message has been recorded.
- **Contacted · waiting** — you sent a message and the prospect has not replied yet.
- **Needs your reply** — the most recent message is from the prospect.
- **Active conversation** — both sides have exchanged messages and the latest message is yours.
- **Elimination Zone** — you explicitly decided not to communicate with this person.

The GUI shows these states with colors and category counts.

## Workflow for a URL prospect

1. Click **Add prospect by URL**.
2. Paste the person's LinkedIn profile URL.
3. The assistant opens the profile in its dedicated Chrome window and reads the visible profile text.
4. The person is added to the DB as **Not connected**.
5. Click **Generate initial**. The prompt explicitly treats them as a person you are not connected to, so it will not pretend you already know them.
6. Copy the draft into the appropriate LinkedIn UI and send manually.
7. Save the exact text you actually sent with **Save as sent**.
8. When they reply, paste the exact response and click **Save prospect reply**.
9. Click **Generate reply** for the next response.

## Workflow for connections.csv

1. Put `connections.csv` at the project root.
2. Start `Start LinkedIn Agent.bat`.
3. The CSV is imported/updated without deleting existing conversations or elimination decisions.
4. Search/select a prospect and use the same conversation workflow.

## Colors

Blue = not contacted  
Orange = contacted / waiting  
Red = needs your reply  
Green = active two-way conversation  
Gray = elimination zone

## Browser profile

The URL-based profile reader uses a dedicated persistent Chrome profile in `.linkedin-browser-profile`. Log in to LinkedIn in that Chrome window once; subsequent profile reads reuse the session. Only page text visible to the browser is captured.

## Data

- `connections.csv` is local/private and ignored by Git.
- `linkedin_agent.db` is local/private and ignored by Git.
- `.linkedin-browser-profile/` is local/private and should never be committed.
- Conversation history and prospect decisions persist locally.

## Ollama

Defaults:

- URL: `http://127.0.0.1:11434`
- Model: `gemma4:31b-cloud`

Override them with `OLLAMA_URL` and `OLLAMA_MODEL` environment variables.
