from __future__ import annotations


class ToolInputError(ValueError):
    """A tool failure the caller can fix by changing its arguments.

    Carries a structured ``error_code`` so the tool executor can classify the failure from a
    typed field instead of matching on the message text, and so the model receives a code it
    can branch on rather than an opaque ``tool_execution_failed``.
    """

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code
