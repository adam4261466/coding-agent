import os
import subprocess
import glob as globmod
import re
import sys
import threading
import traceback
from datetime import datetime

# ---------------------------------------------------------------------------
# Inline debugging: prints to stderr and appends to agent_debug.log
# (set AGENT_DEBUG=0 to disable)
# ---------------------------------------------------------------------------
_DEBUG_ON = os.environ.get("AGENT_DEBUG", "1") != "0"
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_debug.log")


def _dbg(msg: str):
    if not _DEBUG_ON:
        return
    try:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        thread = threading.current_thread().name
        line = f"[{ts}] [tools] [{thread}] {msg}"
        print(line[:4000], file=sys.stderr, flush=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


MAX_OUTPUT = 8000
CUSTOM_TOOLS = {}


def create_tool(name: str, description: str, parameters: dict, code: str) -> str:
    _dbg(f"create_tool name={name!r} desc={description[:100]!r}")
    try:
        safe_builtins = {
            "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
            "enumerate": enumerate, "filter": filter, "float": float,
            "format": format, "frozenset": frozenset, "int": int, "len": len,
            "list": list, "map": map, "max": max, "min": min, "print": print,
            "range": range, "repr": repr, "reversed": reversed, "round": round,
            "set": set, "slice": slice, "sorted": sorted, "str": str,
            "sum": sum, "tuple": tuple, "type": type, "zip": zip,
            "__import__": __import__,
        }

        exec_env = {"__builtins__": safe_builtins}
        try:
            exec(code, exec_env)
        except Exception as e:
            _dbg(f"EXCEPTION create_tool exec() of {name}: {e}\n{traceback.format_exc()}")
            return f"Error creating tool: {e}"

        func_name = None
        for k in exec_env:
            if callable(exec_env[k]) and not k.startswith("_"):
                func_name = k
                break

        if not func_name:
            _dbg(f"create_tool {name}: no callable function found in provided code")
            return "Error: no function found in code"

        func = exec_env[func_name]

        def wrapper(**kwargs):
            _dbg(f"custom tool {name} called kwargs={kwargs}")
            try:
                out = str(func(**kwargs))
                _dbg(f"custom tool {name} OK -> {out[:200]!r}")
                return out
            except Exception as e:
                _dbg(f"EXCEPTION custom tool {name}: {e}\n{traceback.format_exc()}")
                raise

        tool_entry = {
            "description": description,
            "function": wrapper,
            "parameters": parameters,
            "custom": True,
        }

        CUSTOM_TOOLS[name] = tool_entry
        TOOLS[name] = tool_entry
        _dbg(f"create_tool OK: '{name}' registered")

        return f"Created tool '{name}'. You can now use it."

    except Exception as e:
        _dbg(f"EXCEPTION create_tool {name}: {e}\n{traceback.format_exc()}")
        return f"Error creating tool: {e}"


def delete_tool(name: str):
    _dbg(f"delete_tool {name!r}")
    CUSTOM_TOOLS.pop(name, None)
    TOOLS.pop(name, None)


def cleanup_tools():
    _dbg(f"cleanup_tools removing={list(CUSTOM_TOOLS.keys())}")
    for name in list(CUSTOM_TOOLS.keys()):
        CUSTOM_TOOLS.pop(name, None)
        TOOLS.pop(name, None)


def read_file(path: str, offset: int = 0, limit: int = 500) -> str:
    _dbg(f"read_file path={path!r} offset={offset} limit={limit}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(0, offset)
        end = min(total, start + limit)
        output_lines = []
        for i, line in enumerate(lines[start:end], start=start + 1):
            output_lines.append(f"{i}: {line.rstrip()}")
        result = "\n".join(output_lines)
        if len(result) > MAX_OUTPUT:
            result = result[:MAX_OUTPUT] + "\n... (truncated)"
        _dbg(f"read_file OK {path!r} total_lines={total} returned={end - start} bytes={len(result)}")
        return result
    except Exception as e:
        _dbg(f"EXCEPTION read_file {path!r}: {e}\n{traceback.format_exc()}")
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    _dbg(f"write_file path={path!r} bytes={len(content)}")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        _dbg(f"write_file OK {path!r}")
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        _dbg(f"EXCEPTION write_file {path!r}: {e}\n{traceback.format_exc()}")
        return f"Error: {e}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    _dbg(f"edit_file path={path!r} old_len={len(old_string)} new_len={len(new_string)}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if old_string not in content:
            _dbg(f"edit_file FAIL: old_string not found in {path}")
            return f"Error: old_string not found in {path}"
        count = content.count(old_string)
        if count > 1:
            _dbg(f"edit_file FAIL: {count} matches in {path}, need more context")
            return f"Error: found {count} matches, provide more context"
        new_content = content.replace(old_string, new_string, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        _dbg(f"edit_file OK {path!r}")
        return f"Edited {path}"
    except Exception as e:
        _dbg(f"EXCEPTION edit_file {path!r}: {e}\n{traceback.format_exc()}")
        return f"Error: {e}"


def run_bash(command: str, timeout: int = 30) -> str:
    _dbg(f"run_bash cmd={command[:200]!r} timeout={timeout}s cwd={os.getcwd()!r}")
    t0 = datetime.now()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
        )
        output = result.stdout + result.stderr
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n... (truncated)"
        elapsed = (datetime.now() - t0).total_seconds()
        _dbg(f"run_bash OK exit={result.returncode} elapsed={elapsed:.1f}s "
             f"stdout_len={len(result.stdout)} stderr_len={len(result.stderr)} "
             f"output={output[:200]!r}")
        return output if output else "(no output)"
    except subprocess.TimeoutExpired:
        _dbg(f"run_bash TIMEOUT after {timeout}s: {command[:200]!r}")
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        _dbg(f"EXCEPTION run_bash {command[:200]!r}: {e}\n{traceback.format_exc()}")
        return f"Error: {e}"


def glob_search(pattern: str) -> str:
    _dbg(f"glob_search pattern={pattern!r}")
    try:
        matches = globmod.glob(pattern, recursive=True)
        if not matches:
            _dbg("glob_search: no matches")
            return "No matches found"
        result = "\n".join(matches[:200])
        if len(matches) > 200:
            result += f"\n... ({len(matches)} total, showing 200)"
        _dbg(f"glob_search OK n={len(matches)}")
        return result
    except Exception as e:
        _dbg(f"EXCEPTION glob_search {pattern!r}: {e}\n{traceback.format_exc()}")
        return f"Error: {e}"


def grep_search(pattern: str, path: str = ".", include: str = None) -> str:
    _dbg(f"grep_search pattern={pattern!r} path={path!r} include={include!r}")
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        _dbg(f"grep_search invalid regex: {e}")
        return f"Error: invalid regex: {e}"

    results = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv"}]
        for fname in files:
            if include and not globmod.fnmatch(fname, include):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{fpath}:{i}: {line.rstrip()}")
                            if len(results) >= 100:
                                break
            except Exception:
                continue
            if len(results) >= 100:
                break
        if len(results) >= 100:
            break

    if not results:
        _dbg("grep_search: no matches")
        return "No matches found"
    _dbg(f"grep_search OK n={len(results)}")
    return "\n".join(results)


def list_dir(path: str = ".") -> str:
    _dbg(f"list_dir path={path!r}")
    try:
        entries = []
        for entry in os.scandir(path):
            prefix = "  " if entry.is_file() else "d "
            entries.append(f"{prefix}{entry.name}")
        result = "\n".join(sorted(entries)) if entries else "(empty directory)"
        _dbg(f"list_dir OK n_entries={len(entries)}")
        return result
    except Exception as e:
        _dbg(f"EXCEPTION list_dir {path!r}: {e}\n{traceback.format_exc()}")
        return f"Error: {e}"


def open_file(path: str) -> str:
    _dbg(f"open_file path={path!r}")
    try:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            _dbg(f"open_file FAIL: {path} does not exist")
            return f"Error: {path} does not exist"
        os.startfile(abs_path)
        _dbg(f"open_file OK {path!r}")
        return f"Opened {path}"
    except Exception as e:
        _dbg(f"EXCEPTION open_file {path!r}: {e}\n{traceback.format_exc()}")
        return f"Error opening file: {e}"


TOOLS = {
    "read_file": {
        "description": "Read a file's contents. Returns numbered lines.",
        "function": read_file,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "offset": {"type": "integer", "description": "Line number to start from (0-based)", "default": 0},
                "limit": {"type": "integer", "description": "Max lines to read", "default": 500},
            },
            "required": ["path"],
        },
    },
    "write_file": {
        "description": "Write content to a file. Creates directories automatically.",
        "function": write_file,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    "edit_file": {
        "description": "Replace exact text in a file. Use for precise edits.",
        "function": edit_file,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact text to replace"},
                "new_string": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    "bash": {
        "description": "Run a shell command to open apps, run programs, or execute system tasks. Use Start-Process to launch desktop apps like Spotify, Notepad, Discord, etc.",
        "function": run_bash,
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run. To open an app: Start-Process \"appname\" (e.g. Start-Process \"spotify\")"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
            },
            "required": ["command"],
        },
    },
    "glob": {
        "description": "Find files matching a glob pattern (e.g. **/*.py, src/**/*.ts).",
        "function": glob_search,
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern"},
            },
            "required": ["pattern"],
        },
    },
    "grep": {
        "description": "Search file contents with regex. Returns matching lines with file:line.",
        "function": grep_search,
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory to search in", "default": "."},
                "include": {"type": "string", "description": "File pattern to include (e.g. *.py)"},
            },
            "required": ["pattern"],
        },
    },
    "list_dir": {
        "description": "List files and directories at a path.",
        "function": list_dir,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path", "default": "."},
            },
        },
    },
    "open_file": {
        "description": "Open a file with the default system application (e.g. Notepad, VS Code, browser).",
        "function": open_file,
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to open"},
            },
            "required": ["path"],
        },
    },
    "create_tool": {
        "description": "Create a new custom tool when no existing tool fits the task. Write a Python function that does what you need.",
        "function": create_tool,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Tool name (snake_case)"},
                "description": {"type": "string", "description": "What this tool does"},
                "parameters": {
                    "type": "object",
                    "description": "JSON Schema for the tool's parameters",
                    "properties": {
                        "type": {"type": "string", "default": "object"},
                        "properties": {"type": "object"},
                        "required": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "code": {"type": "string", "description": "Python function code. Function must match the tool name."},
            },
            "required": ["name", "description", "parameters", "code"],
        },
    },
}

from browser_dom import register_browser_tools
_dbg("registering browser tools...")
register_browser_tools(TOOLS)
_dbg(f"browser tools registered, total tools={len(TOOLS)}")

from linkedin_data import linkedin_data
TOOLS["linkedin_data"] = {
    "description": "Search the user's LinkedIn data export (offline copy, no browser needed). "
    "Use for: listing/filtering connections (e.g. all 'Amine'), looking up one person's "
    "company/position/URL, companies followed, messages, invitations, learning courses, "
    "and profile info. Look up the person here BEFORE browsing LinkedIn so you know exactly "
    "who to find and their profile URL.",
    "function": linkedin_data,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "What to query: summary, connections (filter by name), connection (one person), companies, messages, invitations, learning, profile",
                "default": "summary",
            },
            "query": {
                "type": "string",
                "description": "Filter keyword, e.g. 'Amine' or a full name",
                "default": "",
            },
            "limit": {"type": "integer", "description": "Max results to return", "default": 30},
        },
    },
}
_dbg(f"tools module fully loaded, total tools={sorted(TOOLS.keys())}")
