return {
  name = "lua_echo",
  description = "Echo back a message from a Lua-defined custom tool.",
  parameters = {
    type = "object",
    properties = {
      message = {
        type = "string",
        description = "Message to echo back.",
      },
      uppercase = {
        type = { "boolean", "null" },
        description = "When true, convert the message to uppercase before returning it.",
      },
    },
    required = { "message" },
    additionalProperties = false,
  },
  handler = function(args)
    local message = args.message or ""
    if args.uppercase then
      message = string.upper(message)
    end
    return {
      ok = true,
      echoed = message,
      source = "lua",
    }
  end,
}
