## Tool-calling protocol

You can call exactly one tool per reply. To call a tool, reply with ONLY a JSON object and no other
text:

{"tool": "<tool name>", "arguments": { ... }}

The result will come back in a `<tool_result>` block. Available tools, with the JSON schema of their
arguments:

$tools
