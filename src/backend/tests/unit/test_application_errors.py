from uuid import uuid4

from src.application.auth_identity.errors import InvalidCredentialsError
from src.application.catalog.management import CaseConflictError
from src.application.common.errors import ApplicationError, EntityAlreadyDeletedError, ValidationError
from src.application.operations.recovery import DependencyError
from src.application.operations.work import FailureCode
from src.application.wallet.errors import InsufficientFundsError


def test_shared_error_interface_preserves_domain_details() -> None:
    error = ValidationError("Invalid value")
    assert isinstance(error, (ApplicationError, ValueError))
    assert error.code == "VALIDATION_ERROR"
    assert error.message == str(error) == "Invalid value"
    assert InvalidCredentialsError().code == "INVALID_CREDENTIALS"
    assert CaseConflictError("STALE_VERSION", "Reload").code == "STALE_VERSION"
    assert InsufficientFundsError("Not enough funds").message == "Not enough funds"
    assert EntityAlreadyDeletedError(entity_name=str(uuid4())).message.startswith("Entity ")
    dependency = DependencyError(FailureCode.TIMEOUT)
    assert dependency.code is FailureCode.TIMEOUT
    assert dependency.message == FailureCode.TIMEOUT.value
