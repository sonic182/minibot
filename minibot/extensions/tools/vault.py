from __future__ import annotations

from pydantic import BaseModel

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.base import ToolContext


class ListSecretsArgs(BaseModel):
    """No arguments."""


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or mb.vault is None:
        return
    vault = mb.vault

    @mb.tool
    async def list_secrets(args: ListSecretsArgs, context: ToolContext) -> dict[str, list[str]]:
        """List the names of the credentials stored in the vault.

        Returns names only — secret values are never readable by you. Each secret is bound in
        configuration to the one destination allowed to use it (an MCP server, for example), and
        that destination resolves it itself. Use this to tell the user which credentials exist.
        """
        return {"names": vault.names()}
