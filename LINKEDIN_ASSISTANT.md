# LinkedIn Conversation CRM

This branch contains only the simple LinkedIn conversation assistant.

## Pipeline

Every prospect is classified from the real conversation stored in `linkedin_agent.db`:

- **Not contacted** — no message has been recorded.
- **Contacted · waiting** — you sent a message and the prospect has not replied yet.
- **Active conversation** — both sides have exchanged at least one message.
- **Needs your reply** — the most recent message is from the prospect.
- **Elimination Zone** — you explicitly decided not to communicate with this person.

The GUI shows these states with colors and category counts, so you can understand the whole pipeline without opening every prospect.

## Workflow

1. Put your LinkedIn export at the project root as `connections.csv`.
2. Start `Start LinkedIn Agent.bat`.
3. The importer finds the real LinkedIn header even when LinkedIn puts a `Notes:` preamble before it.
4. The importer updates prospect information **without deleting conversation history or elimination decisions**.
5. Search/select a prospect.
6. Use **Open LinkedIn** to open the saved profile URL.
7. Use **Generate initial** or **Generate reply** for an Ollama draft.
8. Send the message yourself in LinkedIn.
9. Save the exact outbound message with **Save as sent**.
10. Paste and save the exact prospect response with **Save prospect reply**.
11. Use **Generate reply** for the next response.
12. Use **Move to Elimination Zone** for prospects you never want to contact. A reason is optional.
13. Use **Restore** to bring an eliminated prospect back into the normal pipeline.

## Colors

Blue = not contacted  
Orange = contacted / waiting  
Red = prospect replied / your turn  
Green = two-way active conversation  
Gray = elimination zone

The assistant never sends LinkedIn messages automatically.

## Data

- `connections.csv` is local/private and ignored by Git.
- `linkedin_agent.db` is local/private and ignored by Git.
- Conversation history is preserved when the CSV is imported again.

## Ollama

Defaults:

- URL: `http://127.0.0.1:11434`
- Model: `gemma4:31b-cloud`

Override them with `OLLAMA_URL` and `OLLAMA_MODEL` environment variables.
