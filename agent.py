import json
import re
import sys
import threading
import traceback
import requests
import time
import os
from datetime import datetime
from tools import TOOLS, CUSTOM_TOOLS, delete_tool, cleanup_tools

# Inline debugging: prints to stderr and appends to agent_debug.log
# (set AGENT_DEBUG=0 to disable)
_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [agent] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


SYSTEM_PROMPT = """You are an autonomous WEB NAVIGATION agent that answers questions about websites by driving a real browser (Chrome) step by step.

=================================================================
HOW IT WORKS
=================================================================
- browser_goto(url) opens a page in the agent's Chrome window and lists the page's
  interactive elements with their indexes.
- browser_snapshot() re-lists the current page's elements (same indexes that
  browser_click / browser_type use).
- browser_click(index) clicks an element, browser_type(index, text, press_enter)
  types into a field, browser_scroll(dy) scrolls, browser_press(key) sends a key.
- The browser is the ONLY source of truth: read what it shows NOW. Never rely on
  memory of how a site looked or guess what a page contains.

=================================================================
HOW TO ANSWER A REQUEST
=================================================================
1. Understand the goal.
2. browser_goto(...) the entry page - the homepage, or the URL the user gave you.
3. Read the element list. Click links / tabs / buttons toward the content you
   need. Re-snapshot after every click - the page changes.
4. Use search boxes and fields when you need to find something specific; always
   pick the right suggestion and click the correct button.
5. Answer the user from what the LIVE page shows.

=================================================================
RULES
=================================================================
- ALWAYS inspect the page after navigating: click -> browser_snapshot -> decide
  the next step. Never click an index you have not seen in a snapshot.
- If a snapshot ends with "... more elements (indexes N..M)", that does NOT mean
  the page has no more content - call browser_snapshot(start=N) to page through
  and see the rest of the indexes.
- Use page landmarks to go deeper: nav tabs, search box, "View all" links,
  profile/entity links, etc.
- DYNAMIC PAGES: event pages, profiles, articles, conversations are identified
  by the LIVE browser, not by guessing URLs. Click the real item you see, or
  search for it. Never fabricate a URL.
- MESSAGING (sending a message): open the actual thread/conversation, type the
  message body, and CLICK the Send button - never press Enter to send. If a
  recipient field is involved, always CLICK the correct person in the
  autocomplete dropdown before sending; never send to an unconfirmed recipient.
- VERIFY: never claim you reached a page or saw content you did not. If a click
  does nothing or the page looks wrong, re-snapshot, scroll, or go back and try
  another route.
- LOGIN WALLS: if a page demands login or limits access, tell the user and move
  on - never force credentials.
- Answer from LIVE data (what the browser snapshot shows now).

Current directory: {cwd}
"""

MAX_AGENT_STEPS = 25
TIMEOUT = 600


class Agent:
    def __init__(self, model="gemma4:31b-cloud", base_url="http://localhost:11434"):
        _dbg(f"Agent.__init__ model={model} base_url={base_url}")
        self._model = model
        self.base_url = base_url
        self.messages = []
        self._last_action = None
        self._repeat_count = 0
        self._init_system()

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str):
        _dbg(f"model changed: {self._model} -> {value}")
        self._model = value

    def _init_system(self):
        cwd = os.getcwd()
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT.format(cwd=cwd)}]

    def _get_tool_schemas(self):
        schemas = []
        for name, tool in TOOLS.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            })
        return schemas

    def _call_llm(self, messages):
        start = time.time()
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": self._get_tool_schemas(),
            "stream": False,
            "options": {"num_ctx": 16384, "temperature": 0.3},
        }
        _dbg(f"_call_llm START model={self.model} base_url={self.base_url} "
             f"n_messages={len(messages)} n_tools={len(payload['tools'])}")
        try:
            _dbg("LLM REQUEST:\n" + json.dumps(
                {**payload, "messages": messages[-4:]}, ensure_ascii=False, default=str)[:4000])
        except Exception:
            pass
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=TIMEOUT,
            )
            elapsed = time.time() - start
            _dbg(f"LLM RESPONSE status={resp.status_code} elapsed={elapsed:.1f}s "
                 f"bytes={len(resp.text)}")
            _dbg("LLM BODY:\n" + resp.text[:4000])

            if resp.status_code != 200:
                detail = resp.text[:500]
                try:
                    detail = resp.json().get("error", detail)
                except Exception:
                    pass
                _dbg(f"Ollama {resp.status_code} error. model={self.model} "
                     f"base_url={self.base_url} num_messages={len(messages)} "
                     f"detail={detail}")
                return {"error": f"Ollama returned {resp.status_code}: {detail}"}

            data = resp.json()
            message = data.get("message", {})
            n_calls = len(message.get("tool_calls", []) or [])
            _dbg(f"_call_llm OK content_len={len(message.get('content', ''))} "
                 f"tool_calls={n_calls}")

            return {
                "content": message.get("content", ""),
                "tool_calls": message.get("tool_calls", []),
                "time": round(elapsed, 1),
            }

        except requests.exceptions.ConnectionError as e:
            _dbg(f"EXCEPTION _call_llm (ConnectionError): {e}\n{traceback.format_exc()}")
            return {"error": "Cannot connect to Ollama. Is it running?"}
        except requests.exceptions.Timeout as e:
            _dbg(f"EXCEPTION _call_llm (Timeout after {TIMEOUT}s): {e}\n{traceback.format_exc()}")
            return {"error": f"Timed out after {TIMEOUT}s."}
        except Exception as e:
            _dbg(f"EXCEPTION _call_llm: {e}\n{traceback.format_exc()}")
            return {"error": f"Error: {e}"}

    def _execute_tool(self, name: str, args: dict) -> str:
        if name not in TOOLS:
            _dbg(f"Unknown tool requested: {name}")
            return f"Error: unknown tool '{name}'"
        _dbg(f"_execute_tool {name} args={json.dumps(args, ensure_ascii=False, default=str)[:500]}")
        try:
            result = TOOLS[name]["function"](**args)
            if len(result) > 1000000:
                result = result[:1000000] + "\n... (truncated)"
            if name in CUSTOM_TOOLS:
                delete_tool(name)
            _dbg(f"_execute_tool {name} OK len={len(result)} "
                 f"result={result[:300]!r}")
            return result
        except Exception as e:
            if name in CUSTOM_TOOLS:
                delete_tool(name)
            _dbg(f"EXCEPTION _execute_tool:{name}: {e}\n{traceback.format_exc()}")
            return f"Error executing {name}: {e}"

    def _needs_permission(self, tool_name: str, args: dict) -> bool:
        if tool_name == "bash":
            cmd = args.get("command", "").lower()
            dangerous = ["rm ", "rmdir", "del ", "format", "shutdown", "reboot",
                         "kill", "taskkill", "move ", "ren ", "copy "]
            return any(d in cmd for d in dangerous)
        if tool_name == "write_file":
            path = args.get("path", "")
            return not path.endswith((".py", ".js", ".ts", ".html", ".css", ".json",
                                      ".txt", ".md", ".yaml", ".yml", ".toml", ".cfg"))
        return False

    def _prune_stale_images(self, keep: int = 1):
        img_msgs = [m for m in self.messages if m.get("images")]
        for m in img_msgs[:-keep]:
            m["images"] = []

    def run(self, user_message: str, permission_callback=None) -> str:
        _dbg(f"run() START user_message={user_message!r}")
        self._prune_stale_images(keep=1)
        self._last_action = None
        self._repeat_count = 0

        self.messages.append({"role": "user", "content": user_message})

        for step in range(MAX_AGENT_STEPS):
            _dbg(f"--- run() step {step + 1}/{MAX_AGENT_STEPS} ---")
            sys.stdout.write(f"\033[90m[step {step+1}] thinking...\033[0m ")
            sys.stdout.flush()

            response = self._call_llm(self.messages)

            if "error" in response:
                _dbg(f"run() ABORT: LLM error -> {response['error']}")
                return response["error"]

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])
            elapsed = response.get("time", 0)

            print(f"\033[90m({elapsed}s)\033[0m")

            if content:
                print(f"{content}")

            if not tool_calls:
                _dbg(f"run() DONE (no more tool calls) content_len={len(content)}")
                self.messages.append({"role": "assistant", "content": content})
                return content

            self.messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                args_raw = func.get("arguments", "{}")

                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = args_raw

                if self._needs_permission(name, args) and permission_callback:
                    action_desc = f"{name}({json.dumps(args, ensure_ascii=False)[:200]})"
                    approved = permission_callback(action_desc)
                    if not approved:
                        _dbg(f"PERMISSION DENIED by user: {action_desc}")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": "Permission denied by user.",
                        })
                        print(f"  \033[31mX {name} - denied by user\033[0m")
                        continue

                action_key = (name, json.dumps(args, sort_keys=True))
                if action_key == self._last_action:
                    self._repeat_count += 1
                else:
                    self._last_action = action_key
                    self._repeat_count = 1
                if self._repeat_count >= 3:
                    _dbg(f"LOOP DETECTED: {name} repeated {self._repeat_count}x - stopping")
                    print(f"  \033[31m! {name} repeated {self._repeat_count}x in a row with no "
                          f"page change - stopping loop\033[0m")
                    return (f"Stopped after repeating the same action ({name} {json.dumps(args)}) "
                            f"3 times in a row with no page change. The task looks blocked - "
                            f"inspect the page, try a different route, and move on.")

                print(f"  \033[36m-> {name}({json.dumps(args, ensure_ascii=False)[:200]})\033[0m")
                result = self._execute_tool(name, args)
                preview = result[:150].replace("\n", " ")
                print(f"  \033[33m<- {preview}{'...' if len(result) > 150 else ''}\033[0m")

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        _dbg("run() STOPPED: max steps reached")
        return "(max steps reached)"

    def clear(self):
        _dbg("clear() conversation reset")
        cleanup_tools()
        self.messages = []
        self._init_system()
