"""
Asrār — Main Agent Loop
Receives a task, classifies it, picks the best model,
calls the right tools, and returns a response.
"""

import sys
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime

# Path setup
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend" / "core"))
sys.path.insert(0, str(ROOT / "backend"))

from core.classifier import classify_task
from core.router import route
from providers import get_provider
from providers.base import Message
from tools import web, files, shell, diagnosis

LOG_PATH = ROOT / "logs" / "actions.log"
ASRAR_SYSTEM = (ROOT / "ASRAR.md").read_text(encoding="utf-8")[:2000]

SYSTEM_PROMPT = f"""You are Asrār (أسرار), a powerful agentic AI assistant running on the user's local computer.
You can work with files, create projects, download resources, run commands, and diagnose system issues.

IMPORTANT AGENTIC BEHAVIOR:
- When asked to create, edit, or modify files/projects, ALWAYS use tools
- Use [TOOL:...] markers to execute actions — don't just explain what you would do
- Every action should result in an actual change on the user's computer
- Always verify tools succeeded and report exact paths/results
- Think step-by-step and use tools for each step

Tool usage format: [TOOL:tool_name|arg1=value1,arg2=value2]

Example file creation:
  "I'll create main.py for you now."
  [TOOL:write_file|path=~/myproject/main.py,content=print('Hello World')]
  "Created main.py at ~/myproject/main.py"

Example project creation:
  [TOOL:create_project|name=calculator,base_path=~/Downloads,files={{"main.py":"print('calc')","README.md":"# Calculator"}}]

Your personality: Direct, efficient, action-oriented. Report what you did and the results.
Before running shell commands that modify systems, summarize what they do and ask for confirmation.

Rules:
- Never delete files without explicit confirmation
- Never run destructive commands without warning
- Report the exact file paths where things were created/saved
- If a tool fails, try an alternative approach
"""


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({**entry, "ts": datetime.now().isoformat()}) + "\n")


async def _call_tool(tool_name: str, args: dict) -> str:
    """Route tool calls to the right function."""
    try:
        if tool_name == "web_search":
            query = str(args.get("query", "")).strip()
            if not query:
                return "Error: query required"
            return await web.search_and_summarize(query)

        elif tool_name == "fetch_page":
            url = str(args.get("url", "")).strip()
            if not url:
                return "Error: url required"
            r = await web.fetch_page(url)
            return r.page_content or r.error or "No content"

        elif tool_name == "read_file":
            path = str(args.get("path", "")).strip()
            if not path:
                return "Error: path required"
            r = files.read_file(path)
            return r.content or r.error or "Empty file"

        elif tool_name == "write_file":
            path = str(args.get("path", "")).strip()
            content = str(args.get("content", ""))
            overwrite = str(args.get("overwrite", "false")).lower() in ["true", "1", "yes"]
            if not path:
                return "Error: path required"
            r = files.write_file(path, content, overwrite=overwrite)
            return r.content or r.error

        elif tool_name == "create_project":
            name = str(args.get("name", "")).strip()
            base_path = args.get("base_path") and str(args.get("base_path")).strip() or None
            # files_dict might come as nested dict or string
            files_arg = args.get("files", {})
            if isinstance(files_arg, str):
                import json
                try:
                    files_dict = json.loads(files_arg)
                except:
                    files_dict = {}
            else:
                files_dict = files_arg if isinstance(files_arg, dict) else {}
            
            if not name:
                return "Error: project name required"
            r = files.create_project(name, base_path=base_path, files_dict=files_dict)
            return r.content or r.error

        elif tool_name == "list_dir":
            path = str(args.get("path", ".")).strip()
            r = files.list_dir(path)
            return r.content or r.error

        elif tool_name == "download_url":
            url = str(args.get("url", "")).strip()
            path = args.get("path") and str(args.get("path")).strip() or None
            overwrite = str(args.get("overwrite", "false")).lower() in ["true", "1", "yes"]
            if not url:
                return "Error: url required"
            r = files.download_url(url, path=path, overwrite=overwrite)
            return r.content or r.error

        elif tool_name == "run_command":
            command = str(args.get("command", "")).strip()
            if not command:
                return "Error: command required"
            check = shell.check_command(command)
            if check["blocked"]:
                return f"❌ Blocked: This command is on the safety blocklist."
            if check["needs_confirm"] and not str(args.get("confirmed", "")).lower() in ["true", "1", "yes"]:
                return f"⚠️ This command needs confirmation: `{command}`\nReply with confirmed=true to proceed."
            r = await shell.run(command)
            out = r.stdout or r.stderr or "(no output)"
            return f"Exit {r.returncode}:\n{out}"

        elif tool_name == "diagnose_system":
            mode = str(args.get("mode", "overview")).strip()
            if mode == "full":
                r = await diagnosis.full_diagnosis()
            elif mode == "processes":
                r = await diagnosis.top_processes()
            elif mode == "crashes":
                r = await diagnosis.read_crash_logs()
            else:
                r = await diagnosis.system_overview()
            return r.report or r.error or "No data"

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Tool error ({tool_name}): {str(e)}"


async def run_task(
    user_input: str,
    history: list[Message] | None = None,
    force_model: str | None = None,
) -> dict:
    """
    Main entry point.
    Returns: { response, model_used, task_type, tool_calls }
    """
    history = history or []

    # 1. Classify + route
    decision = route(user_input, force_model=force_model)

    # 2. Get provider
    provider = get_provider(decision.selected_model["provider"])

    if not provider.is_available():
        # Try fallback chain
        for fallback_key in decision.fallback_chain:
            from core.router import load_registry
            registry = load_registry()
            fb_model = registry["models"][fallback_key]
            provider = get_provider(fb_model["provider"])
            if provider.is_available():
                decision.selected_model = fb_model
                decision.selected_model_key = fallback_key
                break
        else:
            return {
                "response": "No available models. Please check your API keys in .env",
                "model_used": None,
                "task_type": decision.task_type,
                "tool_calls": [],
            }

    # 3. Build context with tool awareness
    tool_context = _build_tool_context(decision.task_type, user_input)
    messages = history + [Message(role="user", content=user_input + tool_context)]

    # 4. Call model
    resp = await provider.complete(
        messages=messages,
        model_id=decision.selected_model["id"],
        system=SYSTEM_PROMPT,
        max_tokens=2048,
    )

    tool_calls = []

    if not resp.success:
        response_text = f"Model error: {resp.error}"
    else:
        response_text = resp.content
        # Parse and execute any tool calls embedded in response
        tool_calls = await _execute_embedded_tools(response_text)
        if tool_calls:
            # Append tool results and re-call model for final answer
            tool_summary = "\n".join(f"[{t['tool']}]: {t['result']}" for t in tool_calls)
            followup = messages + [
                Message(role="assistant", content=response_text),
                Message(role="user", content=f"Tool results:\n{tool_summary}\n\nNow give your final answer.")
            ]
            final_resp = await provider.complete(
                messages=followup,
                model_id=decision.selected_model["id"],
                system=SYSTEM_PROMPT,
            )
            if final_resp.success:
                response_text = final_resp.content

    _log({
        "task": user_input[:100],
        "task_type": decision.task_type,
        "model": decision.selected_model["display_name"],
        "tools": [t["tool"] for t in tool_calls],
        "success": resp.success,
    })

    return {
        "response": response_text,
        "model_used": decision.selected_model["display_name"],
        "task_type": decision.task_type,
        "routing_reason": decision.reason,
        "tool_calls": tool_calls,
    }


def _build_tool_context(task_type: str, user_input: str) -> str:
    """Append tool-use instructions to prompt based on task type."""
    hints = {
        "web_research": """\n\n**Tools available:**
- web_search(query=str) — Search the web
- fetch_page(url=str) — Get full page content
- download_url(url=str, path=str) — Save a file locally
Examples:
  [TOOL:web_search|query=latest AI news]
  [TOOL:download_url|url=https://example.com/file.pdf,path=~/Downloads/file.pdf]""",
        "pc_diagnosis": """\n\n**Tool available:**
- diagnose_system(mode=overview|full|processes|crashes) — Check system health
Example:
  [TOOL:diagnose_system|mode=full]""",
        "shell_automation": """\n\n**Tool available:**
- run_command(command=str, confirmed=true) — Run shell commands (with confirmation)
Example:
  [TOOL:run_command|command=mkdir -p ~/mydir,confirmed=true]""",
        "document_work": """\n\n**Tools available:**
- read_file(path=str) — Read any file
- write_file(path=str, content=str, overwrite=true) — Create/edit files
- create_project(name=str, base_path=~/Downloads, files={"file1.txt":"content"}) — Create project directory
- download_url(url=str, path=str) — Download resources
Examples:
  [TOOL:write_file|path=~/test.py,content=print('hello')]
  [TOOL:create_project|name=myapp,base_path=~/Downloads]
  [TOOL:download_url|url=https://example.com/image.jpg,path=~/Downloads/img.jpg]""",
        "general": """\n\n**Available tools:**
- read_file(path=str) — Read files
- write_file(path=str, content=str) — Write files
- create_project(name=str, files={...}) — Create projects
- list_dir(path=str) — List directories
- run_command(command=str) — Run terminal commands (needs confirmation for dangerous ones)
- web_search(query=str) — Search online
- download_url(url=str, path=str) — Download files
Always include the tool arguments. Example: [TOOL:write_file|path=~/myfile.txt,content=hello world]""",
    }
    return hints.get(task_type, hints["general"])


async def _execute_embedded_tools(response: str) -> list[dict]:
    """
    Look for tool call markers in model response and execute them.
    Supports:
    - [TOOL:tool_name|arg1=value1,arg2=value2]  where values can be JSON objects
    - <invoke name="tool_name"><parameter name="key">value</parameter></invoke>
    """
    import re
    import json
    
    results = []
    
    # Parse [TOOL:...] format with smart JSON handling
    pattern = r"\[TOOL:(\w+)\|([^\]]+)\]"
    for match in re.finditer(pattern, response):
        tool_name = match.group(1)
        args_str = match.group(2)
        
        # Parse arguments, handling JSON values properly
        args = _parse_tool_args(args_str)
        
        print(f"[DEBUG] [TOOL:...] {tool_name} with args: {list(args.keys())}")
        if 'files' in args:
            print(f"[DEBUG]   files type: {type(args['files'])}")
            if isinstance(args['files'], dict):
                print(f"[DEBUG]   files dict keys: {list(args['files'].keys())}")
        
        result = await _call_tool(tool_name, args)
        results.append({"tool": tool_name, "args": args, "result": result[:1000]})
    
    # Try regex-based XML extraction (more forgiving)
    try:
        invoke_pattern = r'<invoke\s+name="(\w+)">(.*?)</invoke>'
        for invoke_match in re.finditer(invoke_pattern, response, re.DOTALL):
            tool_name = invoke_match.group(1)
            invoke_content = invoke_match.group(2)
            
            # Extract parameters
            args = {}
            param_pattern = r'<parameter\s+name="([^"]+)">(.+?)</parameter>'
            for param_match in re.finditer(param_pattern, invoke_content, re.DOTALL):
                param_name = param_match.group(1)
                param_value = param_match.group(2).strip()
                
                # Basic unescaping for common XML entities
                param_value = param_value.replace('&lt;', '<').replace('&gt;', '>')
                param_value = param_value.replace('&amp;', '&').replace('&quot;', '"')
                param_value = param_value.replace('&apos;', "'")
                # Unescape escaped quotes in strings
                param_value = param_value.replace('\\"', '"').replace('\\n', '\n')
                
                args[param_name] = param_value
            
            # Try to parse files parameter as JSON if present
            if 'files' in args and isinstance(args['files'], str):
                if '{' in args['files']:
                    try:
                        args['files'] = json.loads(args['files'])
                    except json.JSONDecodeError as e:
                        # Try to fix common issues: escape literal newlines and quotes
                        import re as regex
                        cleaned = args['files']
                        # Escape control characters that aren't properly escaped
                        cleaned = regex.sub(r'([^\\])(\n)', r'\1\\n', cleaned)  # Literal newlines
                        try:
                            args['files'] = json.loads(cleaned)
                        except:
                            print(f"[DEBUG] Still failed to parse files after cleanup: {e}")
                            # If JSON parsing still fails, just leave it as string
                            pass
            
            print(f"[DEBUG] XML invoke {tool_name} with args: {list(args.keys())}")
            if 'files' in args and isinstance(args['files'], dict):
                print(f"[DEBUG]   files dict keys: {list(args['files'].keys())[:5]}")
            
            result = await _call_tool(tool_name, args)
            results.append({"tool": tool_name, "args": args, "result": result[:1000]})
    except Exception as e:
        print(f"[DEBUG] XML extraction failed: {e}")

    return results


def _parse_tool_args(args_str: str) -> dict:
    """
    Parse tool arguments handling JSON values.
    E.g., 'name=test,base_path=~/Downloads,files={"a":"b","c":"d"}'
    """
    import json
    
    args = {}
    i = 0
    while i < len(args_str):
        # Find the key=
        eq_pos = args_str.find('=', i)
        if eq_pos == -1:
            break
        
        key = args_str[i:eq_pos].strip()
        
        # Find the value (could be JSON object, string, or simple value)
        value_start = eq_pos + 1
        
        # Check if value is JSON object or array
        if value_start < len(args_str) and args_str[value_start] in ('{', '['):
            # Find matching closing bracket
            bracket_count = 0
            closing_pos = value_start
            in_string = False
            escape_next = False
            
            while closing_pos < len(args_str):
                char = args_str[closing_pos]
                
                if escape_next:
                    escape_next = False
                elif char == '\\':
                    escape_next = True
                elif char == '"' and (closing_pos == 0 or args_str[closing_pos-1] != '\\'):
                    in_string = not in_string
                elif not in_string:
                    if char in ('{', '['):
                        bracket_count += 1
                    elif char in ('}', ']'):
                        bracket_count -= 1
                        if bracket_count == 0:
                            closing_pos += 1
                            break
                
                closing_pos += 1
            
            value_str = args_str[value_start:closing_pos]
            try:
                args[key] = json.loads(value_str)
            except json.JSONDecodeError:
                args[key] = value_str
            
            i = closing_pos
            # Skip comma if present
            if i < len(args_str) and args_str[i] == ',':
                i += 1
        else:
            # Find next comma
            comma_pos = args_str.find(',', value_start)
            if comma_pos == -1:
                value = args_str[value_start:].strip()
                i = len(args_str)
            else:
                value = args_str[value_start:comma_pos].strip()
                i = comma_pos + 1
            
            # Try to parse as JSON if it looks like JSON
            if value.startswith('{') or value.startswith('['):
                try:
                    args[key] = json.loads(value)
                except:
                    args[key] = value
            else:
                args[key] = value
    
    return args


# CLI test
if __name__ == "__main__":
    async def main():
        test_tasks = [
            "What are the top processes using CPU on my machine?",
            "Search the web for latest news on open source AI models",
            "List files in the current directory",
        ]
        for task in test_tasks:
            print(f"\n{'='*60}")
            print(f"Task: {task}")
            result = await run_task(task)
            print(f"Model : {result['model_used']}")
            print(f"Type  : {result['task_type']}")
            print(f"Answer: {result['response'][:300]}...")

    asyncio.run(main())
