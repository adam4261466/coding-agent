import json
import os
import traceback
from datetime import datetime

LOG_PATH = os.path.join(os.getcwd(), "agent_debug.log")
DEBUG = os.environ.get("AGENT_DEBUG", "1") != "0"  # on by default; set AGENT_DEBUG=0 to disable


def _write(block: str):
    if not DEBUG:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {block}\n")
    except Exception:
        pass


def log_llm_request(payload: dict):
    safe = dict(payload)
    # trim messages for readability but keep last few full
    msgs = safe.get("messages", [])
    trimmed = msgs[-6:] if len(msgs) > 6 else msgs
    safe["messages"] = trimmed
    safe["_messages_total"] = len(msgs)
    _write("LLM REQUEST:\n" + json.dumps(safe, indent=2, ensure_ascii=False)[:4000])


def log_llm_response(status_code, text, elapsed=None):
    _write(f"LLM RESPONSE (status={status_code}, elapsed={elapsed}s):\n{text[:4000]}")


def log_tool_call(name, args, result):
    _write(f"TOOL CALL: {name}({json.dumps(args, ensure_ascii=False)[:500]})\n"
           f"RESULT: {str(result)[:1000]}")


def log_exception(context: str, exc: Exception):
    _write(f"EXCEPTION in {context}: {exc}\n{traceback.format_exc()}")


def log_info(msg: str):
    _write(f"INFO: {msg}")