from src.application.common.errors import ApplicationError


class WalletError(ApplicationError):
    pass


class WalletInvariantError(WalletError):
    """Invalid financial state or malformed trusted input."""


class InsufficientFundsError(WalletError):
    pass


class WalletConflictError(WalletError):
    """Business source reuse, active hold conflict, or invalid lifecycle transition."""


class RewardNotEligibleError(WalletError):
    pass


class ReservationNotFoundError(WalletError):
    """Missing reservation, including a reservation belonging to another owner."""


class WalletWriteConflictError(WalletError):
    """Adapter CAS/uniqueness failure: abort the outer UoW, never continue it."""
