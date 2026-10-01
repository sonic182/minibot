from minibot.core.channels import ChannelCapabilities

TELEGRAM_CHANNEL_CAPABILITIES = ChannelCapabilities(
    supports_tool_approval=True,
    supports_reply_targets=True,
    supports_formatted_parse_error_retry=True,
    supports_file_attachment_delivery=True,
    reply_context_label="Telegram reply context",
    format_repair_instructions=(
        "Rewrite the same answer with valid Telegram-compatible formatting.\n"
        "Keep kind as markdown or html only if valid for Telegram, otherwise use text.\n"
        "For markdown, write normal Markdown (do not pre-escape Telegram MarkdownV2)."
    ),
)
