import { DestroyRef, inject, NgZone } from '@angular/core';

/** Refresh read models without replacing an editor's dirty state. Call in injection context. */
export function autoRefresh(
  refresh: () => void | Promise<void>,
  options: { blocked?: () => boolean; shouldPoll?: () => boolean } = {},
): void {
  const destroy = inject(DestroyRef);
  const zone = inject(NgZone);
  let running = false;
  let last = 0;
  const run = () => {
    if (
      destroy.destroyed ||
      document.visibilityState !== 'visible' ||
      options.blocked?.() ||
      running ||
      Date.now() - last < 1000
    )
      return;
    running = true;
    last = Date.now();
    void Promise.resolve()
      .then(() => zone.run(refresh))
      .catch(() => undefined)
      .finally(() => (running = false));
  };
  zone.runOutsideAngular(() => {
    window.addEventListener('focus', run);
    document.addEventListener('visibilitychange', run);
  });
  const timer = options.shouldPoll
    ? setInterval(() => {
        if (options.shouldPoll?.()) run();
      }, 30_000)
    : undefined;
  destroy.onDestroy(() => {
    window.removeEventListener('focus', run);
    document.removeEventListener('visibilitychange', run);
    if (timer !== undefined) clearInterval(timer);
  });
}
