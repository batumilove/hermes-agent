"""Diagnostic-only site hook: fixed-schema launcher telemetry, never environment/argv/content."""
import hashlib
import json
import os
from pathlib import Path
import sys

LOG = Path(globals().get('_PR437_LOG', Path(__file__).with_name('pr437-launcher-trace.jsonl')))
SOURCE = Path(globals().get('_PR437_SOURCE', __file__))
SOURCE_SHA256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest() if SOURCE.is_file() else None


def instrument_source(source, log):
    hook = Path(__file__).read_text(encoding='utf-8')
    payload = "_PR437_LOG = " + repr(str(log)) + "\n_PR437_SOURCE = __file__\n" + hook
    entry = '\nexec(' + repr(payload) + ')\n'
    anchor = 'from __future__ import annotations\n'
    return source.replace(anchor, anchor + entry, 1) if anchor in source else entry + source
FUNCTIONS = {'mint_launcher', 'stage_launcher', '_load_script_maker', '_store_script_maker', '_retire_exe', '_write_atomic', 'make', '_store_python'}

def emit(row):
    try:
        row.update(pid=os.getpid(), interpreter=sys.executable, source=str(SOURCE), source_sha256=SOURCE_SHA256)
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
    except OSError:
        pass

def trace(frame, event, arg):
    if not frame.f_code.co_filename.replace('\\', '/').endswith('/hermes_cli/_launchers.py') or frame.f_code.co_name not in FUNCTIONS:
        return None
    row = {'event': event, 'function': frame.f_code.co_name, 'line': frame.f_lineno}
    if event == 'call':
        stack = []
        caller = frame.f_back
        while caller is not None and len(stack) < 8:
            stack.append({'file': caller.f_code.co_filename, 'function': caller.f_code.co_name, 'line': caller.f_lineno})
            caller = caller.f_back
        row['stack'] = stack
    if event == 'exception':
        exc = arg[1]
        row.update(exception=type(exc).__name__, errno=getattr(exc, 'errno', None), winerror=getattr(exc, 'winerror', None))
    for key in ('name', 'python_exe', 'out_dir', 'repo_root', 'package', 'existing', 'candidate', 'target', 'exe', 'aside', 'staging', 'runtime'):
        value = frame.f_locals.get(key)
        if isinstance(value, (str, Path)):
            row[key] = str(value)
    maker = frame.f_locals.get('script_maker_cls')
    if maker is not None:
        row['maker_module'] = getattr(maker, '__module__', '<unknown>')
    if event == 'return':
        row['returned_none'] = arg is None
        if isinstance(arg, Path):
            row['returned_path'] = str(arg)
        if isinstance(arg, type):
            row['returned_class_module'] = arg.__module__
    package = frame.f_locals.get('package')
    if isinstance(package, Path) and event in ('line', 'return'):
        source = package / 'scripts.py'
        row['distlib_exists'] = source.is_file()
        if source.is_file():
            try:
                row['distlib_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            except OSError:
                pass
    emit(row)
    return trace

emit({'event': 'hook_loaded'})
sys.settrace(trace)
