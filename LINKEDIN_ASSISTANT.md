# LinkedIn Conversation CRM

This branch contains only the simple LinkedIn conversation assistant.

## Prospect sources

Prospects can come from either source:

- `connections.csv` — your existing LinkedIn connections.
- **Direct LinkedIn profile URL** — a person you are not connected to.

Direct-URL prospects are stored as **Not connected** and remain in the same CRM pipeline as CSV connections.

## Pipeline

Every prospect is classified from the real conversation stored in `linkedin_agent.db`:

- **Not contacted** — no message has been recorded.
- **Contacted · waiting** — you sent a message and the prospect has not replied yet.
- **Needs your reply** — the most recent message is from the prospect.
- **Active conversation** — both sides have exchanged messages.
- **Elimination Zone** — you explicitly decided not to communicate with this person.

The GUI shows these states with colors and category counts.

## Existing connections

1. Put the LinkedIn export at the project root as `connections.csv`.
2. Start `Start LinkedIn Agent.bat`.
3. The importer finds the real LinkedIn header even when LinkedIn puts a `Notes:` preamble before it.
4. The importer updates prospect information without deleting conversation history or elimination decisions.

## Add someone by profile URL

1. Start Chrome normally and sign in to LinkedIn.
2. In Chrome, open `chrome://inspect/#remote-debugging`.
3. Enable **Remote Debugging** and allow the connection if Chrome asks.
4. Start the LinkedIn assistant.
5. Click **＋ Add prospect by URL**.
6. Paste the person's full LinkedIn profile URL.
7. The assistant attaches to the existing Chrome session, opens that profile, and reads visible profile information.
8. The person is saved as **Not connected · Added from profile URL**.
9. Click **Generate initial** to create the personalized first-contact draft.

The assistant does **not** launch a separate LinkedIn browser profile for profile reading and does not send connection requests or messages automatically.

## Conversation workflow

1. Search/select one prospect.
2. Use **Open LinkedIn** or the profile reader to open/read the saved URL.
3. Use **Generate initial** or **Generate reply** for an Ollama draft.
4. Send the message yourself in LinkedIn.
5. Save the exact outbound message with **Save as sent**.
6. Paste and save the exact prospect response with **Save prospect reply**.
7. Use **Generate reply** for the next response.
8. Use **Move to Elimination Zone** for prospects you never want to contact. A reason is optional.
9. Use **Restore** to bring an eliminated prospect back.

## Colors

Blue = not contacted  
Orange = contacted / waiting  
Red = prospect replied / your turn  
Green = two-way active conversation  
Gray = elimination zone

## Data

- `connections.csv` is local/private and ignored by Git.
- `linkedin_agent.db` is local/private and ignored by Git.
- Conversation history and elimination decisions persist across CSV imports.
- Profile context read from direct URLs is stored locally in the database.

## Ollama

Defaults:

- URL: `http://127.0.0.1:11434`
- Model: `gemma4:31b-cloud`

Override them with `OLLAMA_URL` and `OLLAMA_MODEL` environment variables.

## Chrome connection

The profile reader uses Chrome DevTools Protocol (CDP) and defaults to:

`http://127.0.0.1:9222`

Override this with `LINKEDIN_CDP_URL` if your Chrome remote-debugging endpoint uses another address.
