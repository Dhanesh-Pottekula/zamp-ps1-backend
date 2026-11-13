"""Master-data refusals: why a vendor or a purchase order cannot be created."""

from typing import Final, Literal


class _Types:
    """The refusal reasons and the error that carries one."""

    type Reason = Literal["invalid", "unknown", "conflict"]

    class MasterError(Exception):
        """A vendor or PO that cannot be created; the message is one sentence for the person who entered it."""

        def __init__(self, reason: _Types.Reason, message: str) -> None:
            """Keep the reason next to the message."""
            super().__init__(message)
            self.reason: Final = reason


class MasterTypes:
    """Public master-data types."""

    type Reason = _Types.Reason
    MasterError = _Types.MasterError
