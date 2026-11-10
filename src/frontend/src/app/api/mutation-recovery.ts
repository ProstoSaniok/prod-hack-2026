import { HttpErrorResponse } from '@angular/common/http';
import { TimeoutError } from 'rxjs';

/** An ambiguous write must be retried with its original request and idempotency key. */
export function uncertainMutation(error: unknown): boolean {
  if (error instanceof TimeoutError) return true;
  if (!(error instanceof HttpErrorResponse)) return false;
  return (
    error.status === 0 ||
    (error.status >= 200 && error.status < 300) ||
    error.status >= 500 ||
    (error.status === 409 && error.error?.code === 'OPERATION_IN_PROGRESS')
  );
}
