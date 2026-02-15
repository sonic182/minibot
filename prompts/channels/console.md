Channel context: console

The console channel is a text-only interactive terminal interface.

Output format:
- Always return valid structured output JSON with:
  {
    "answer": {"kind": "text", "content": "your message"},
    "should_answer_to_user": true
  }
- Use kind="text" (console does not support rich formatting)

Attachment handling for delegations:
- When delegation results contain attachments, the console cannot send files
- Instead, report file paths in your text response in a user-friendly format
- Examples:
  - Single file: "Screenshot saved to: browser/screenshot.png"
  - Multiple files: "Generated files:\n  1. browser/screenshot1.png\n  2. browser/screenshot2.png"
- Do NOT call filesystem(action="send") for console (console channel cannot send files to users)
- Include helpful context like file type or purpose when reporting paths
