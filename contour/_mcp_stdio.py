"""Minimal, dependency-free MCP server over stdio (JSON-RPC 2.0, one message per line).

Implements the subset of the Model Context Protocol a tool-only server needs:
initialize, notifications/initialized, ping, tools/list and tools/call. Kept in the
standard library so the package installs with no third-party dependencies and works
with any MCP client regardless of which SDK version the client uses.

Invariants (tested in tests/test_mcp_server.py):
- stdout carries protocol messages only; logs go to stderr.
- every request with an id gets exactly one response; notifications get none.
- a tool that raises returns a result with isError=true, never a crashed server.
"""
import json
import sys
import traceback

SUPPORTED_PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)


class ToolError(Exception):
    """Raised by a tool for an expected failure; reported to the client as isError."""


class Tool:
    def __init__(self, name, description, input_schema, func, read_only=True, title=None):
        self.name, self.description, self.input_schema, self.func = name, description, input_schema, func
        self.read_only, self.title = read_only, title or name

    def spec(self):
        return {
            'name': self.name,
            'title': self.title,
            'description': self.description,
            'inputSchema': self.input_schema,
            'annotations': {'readOnlyHint': self.read_only, 'destructiveHint': False,
                            'openWorldHint': False, 'title': self.title},
        }


class StdioServer:
    def __init__(self, name, version, instructions=''):
        self.name, self.version, self.instructions = name, version, instructions
        self.tools = {}

    def tool(self, name, description, input_schema, read_only=True, title=None):
        def register(func):
            self.tools[name] = Tool(name, description, input_schema, func, read_only, title)
            return func
        return register

    # -- request handling -------------------------------------------------
    def handle(self, msg):
        """Return the response dict for one incoming message, or None for a notification."""
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
            return _error(msg.get('id') if isinstance(msg, dict) else None, INVALID_REQUEST, 'invalid request')
        method, params, has_id = msg['method'], msg.get('params') or {}, 'id' in msg
        if not has_id:
            return None  # notifications (initialized, cancelled, ...) need no answer
        rid = msg['id']
        try:
            if method == 'initialize':
                asked = params.get('protocolVersion')
                version = asked if asked in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
                return _result(rid, {
                    'protocolVersion': version,
                    'capabilities': {'tools': {'listChanged': False}},
                    'serverInfo': {'name': self.name, 'version': self.version},
                    'instructions': self.instructions,
                })
            if method == 'ping':
                return _result(rid, {})
            if method == 'tools/list':
                return _result(rid, {'tools': [t.spec() for t in self.tools.values()]})
            if method == 'tools/call':
                return _result(rid, self._call(params))
            return _error(rid, METHOD_NOT_FOUND, f'method not found: {method}')
        except Exception as exc:  # protocol-level failure, not a tool failure
            traceback.print_exc(file=sys.stderr)
            return _error(rid, INTERNAL_ERROR, str(exc))

    def _call(self, params):
        name, args = params.get('name'), params.get('arguments') or {}
        tool = self.tools.get(name)
        if tool is None:
            return _tool_error(f'unknown tool: {name}')
        missing = [k for k in tool.input_schema.get('required', []) if k not in args]
        if missing:
            return _tool_error(f'missing required argument(s): {", ".join(missing)}')
        unknown = sorted(set(args) - set(tool.input_schema.get('properties', {})))
        if unknown:
            return _tool_error(f'unknown argument(s): {", ".join(unknown)}')
        try:
            out = tool.func(**args)
        except ToolError as exc:
            return _tool_error(str(exc))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return _tool_error(f'{type(exc).__name__}: {exc}')
        return {'content': [{'type': 'text', 'text': json.dumps(out, indent=2, sort_keys=True)}],
                'structuredContent': out, 'isError': False}

    # -- transport --------------------------------------------------------
    def serve(self, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                resp = _error(None, PARSE_ERROR, 'parse error')
            else:
                resp = self.handle(msg)
            if resp is not None:
                stdout.write(json.dumps(resp, separators=(',', ':')) + '\n')
                stdout.flush()


def _result(rid, result):
    return {'jsonrpc': '2.0', 'id': rid, 'result': result}


def _error(rid, code, message):
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def _tool_error(text):
    return {'content': [{'type': 'text', 'text': text}], 'isError': True}
