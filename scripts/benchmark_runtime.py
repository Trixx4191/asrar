#!/usr/bin/env python3
"""Offline request-size benchmark. No credentials, providers, or billable calls.

This isolates tool menus and in-task context shaping, using the same current
system prompt on both sides. Estimates are characters / 4, not actual usage
or a claim about production latency. Use real task traces for those measures.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from core.agent import SYSTEM_PROMPT, TOOL_SCHEMAS
from core.context import prepare_messages
from core.runtime import payload_chars
from core.tool_catalog import ToolCatalog


def benchmark() -> dict:
    full = [s for s in TOOL_SCHEMAS if s['function']['name'] != 'discover_tools']
    full_size = payload_chars(full)
    menus = []
    for task in ['fast_chat', 'coding', 'document_work', 'web_research', 'analysis']:
        catalog = ToolCatalog(TOOL_SCHEMAS)
        catalog.configure(task)
        size = payload_chars(catalog.schemas)
        menus.append({'task': task, 'before_tools': len(full), 'after_tools': len(catalog.schemas),
                      'before_schema_chars': full_size, 'after_schema_chars': size,
                      'schema_reduction_percent': round(100 * (1 - size / full_size), 1)})
    catalog = ToolCatalog(TOOL_SCHEMAS)
    catalog.configure('coding')
    messages = [{'role': 'user', 'content': 'Inspect source and test logs, repair the defect and verify the change. Preserve user data.'}]
    before = after = 0
    for iteration in range(8):
        before += math.ceil((len(SYSTEM_PROMPT) + full_size + payload_chars(messages)) / 4)
        after += math.ceil((len(SYSTEM_PROMPT) + payload_chars(catalog.schemas) + payload_chars(prepare_messages(messages))) / 4)
        calls = [{'id': f'call-{iteration}-{i}', 'type': 'function', 'function': {
            'name': 'read_file', 'arguments': json.dumps({'path': f'source-{i}.py'})}} for i in range(2)]
        messages.append({'role': 'assistant', 'content': 'Inspecting relevant evidence.', 'tool_calls': calls})
        for i, call in enumerate(calls):
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'name': 'read_file',
                             'content': 'BEGIN SOURCE\n' + f'line {iteration}-{i}: useful project evidence\n' * 600 + 'END: verification result retained'})
    return {'mode': 'offline; zero provider calls', 'estimate_method': 'characters / 4; not billable-token accounting',
            'initial_tool_menus': menus,
            'eight_request_coding_task': {'before_estimated_input_tokens': before,
                'after_estimated_input_tokens': after, 'estimated_reduction_percent': round(100 * (1-after/before), 1),
                'before_final_context_chars': payload_chars(messages),
                'after_final_context_chars': payload_chars(prepare_messages(messages))}}


if __name__ == '__main__':
    print(json.dumps(benchmark(), indent=2))
