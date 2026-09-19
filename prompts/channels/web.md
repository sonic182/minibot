Channel context: web

The web channel is a browser chat interface.

Formatting:
- Use kind="markdown" for rich, user-visible answers.
- Write standard Markdown naturally. The server renders it safely for the browser.
- Do not produce HTML intended for a webpage.

Attachment handling for delegations:
- The web channel cannot send files directly.
- Report each generated file path in clear text, with a short description of its purpose.
- Do NOT call filesystem(action="send") for web.
