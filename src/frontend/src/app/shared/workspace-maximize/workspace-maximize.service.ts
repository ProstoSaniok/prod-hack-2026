import { DOCUMENT, DestroyRef, Injectable, inject, signal } from '@angular/core';

/** A shell layout change: the editor and its navigation keep their original DOM and state. */
@Injectable()
export class WorkspaceMaximizeService {
  private readonly document = inject(DOCUMENT);
  readonly target = signal<HTMLElement | null>(null);
  private host: HTMLElement | null = null;
  private branches: HTMLElement[] = [];
  private opener: HTMLElement | null = null;
  private scroll = { x: 0, y: 0 };
  private restoreFrame = 0;
  private animation: Animation | null = null;
  private pageOverflow = '';

  constructor() {
    const window = this.document.defaultView;
    const motion = window?.matchMedia('(prefers-reduced-motion: reduce)');
    const finishLayout = () => {
      this.animation?.cancel();
      this.animation = null;
    };
    window?.addEventListener('resize', finishLayout);
    motion?.addEventListener('change', finishLayout);
    inject(DestroyRef).onDestroy(() => {
      window?.removeEventListener('resize', finishLayout);
      motion?.removeEventListener('change', finishLayout);
      this.close(false);
    });
  }

  connect(host: HTMLElement): void {
    this.host = host;
  }

  open(target: HTMLElement, opener: HTMLElement): void {
    const content = this.host?.querySelector<HTMLElement>('[data-workspace-content]');
    if (!content?.contains(target)) return;
    const before = target.getBoundingClientRect();
    this.close(false);
    const restoreContentScroll = this.contentScroll(target);
    const window = this.document.defaultView;
    this.scroll = { x: window?.scrollX ?? 0, y: window?.scrollY ?? 0 };
    this.opener = opener;
    let branch = target.parentElement;
    while (branch && content.contains(branch)) {
      branch.setAttribute('data-code-maximize-branch', '');
      this.branches.push(branch);
      if (branch === content) break;
      branch = branch.parentElement;
    }
    target.setAttribute('data-code-maximize-target', '');
    this.host?.classList.add('code-maximized');
    this.pageOverflow = this.document.documentElement.style.overflow;
    this.document.documentElement.style.overflow = 'hidden';
    this.target.set(target);
    this.animate(target, before);
    this.afterLayout(() => {
      restoreContentScroll();
      target.scrollTop = 0;
    });
  }

  close(restore = true): void {
    const window = this.document.defaultView;
    if (this.restoreFrame) window?.cancelAnimationFrame(this.restoreFrame);
    this.restoreFrame = 0;
    const target = this.target();
    const before = target?.getBoundingClientRect();
    this.animation?.cancel();
    this.animation = null;
    if (!target || !before) return;
    const restoreContentScroll = this.contentScroll(target);
    target.removeAttribute('data-code-maximize-target');
    for (const branch of this.branches) branch.removeAttribute('data-code-maximize-branch');
    this.branches = [];
    this.host?.classList.remove('code-maximized');
    this.document.documentElement.style.overflow = this.pageOverflow;
    this.target.set(null);
    const opener = this.opener;
    const scroll = this.scroll;
    this.opener = null;
    if (restore && window) {
      window.scrollTo({ left: scroll.x, top: scroll.y, behavior: 'instant' });
      this.animate(target, before);
      this.afterLayout(() => {
        restoreContentScroll();
        window.scrollTo({ left: scroll.x, top: scroll.y, behavior: 'instant' });
        if (opener?.isConnected && !opener.closest('[inert]'))
          opener.focus({ preventScroll: true });
      });
    }
  }

  private animate(target: HTMLElement, before: DOMRect): void {
    const window = this.document.defaultView;
    this.animation?.cancel();
    if (!window || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const after = target.getBoundingClientRect();
    if (!before.width || !before.height || !after.width || !after.height) return;
    const animation = target.animate(
      [
        {
          transformOrigin: 'top left',
          transform: `translate(${before.left - after.left}px, ${before.top - after.top}px) scale(${before.width / after.width}, ${before.height / after.height})`,
        },
        { transformOrigin: 'top left', transform: 'none' },
      ],
      { duration: 240, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    );
    this.animation = animation;
    void animation.finished.then(
      () => {
        if (this.animation === animation) this.animation = null;
      },
      () => undefined,
    );
  }

  private contentScroll(target: HTMLElement): () => void {
    const positions = Array.from(target.querySelectorAll<HTMLElement>('*'))
      .filter(
        element =>
          element.scrollHeight > element.clientHeight || element.scrollWidth > element.clientWidth,
      )
      .map(element => ({ element, top: element.scrollTop, left: element.scrollLeft }));
    return () => {
      for (const { element, top, left } of positions) {
        if (element.isConnected) {
          element.scrollTop = top;
          element.scrollLeft = left;
        }
      }
    };
  }

  private afterLayout(restore: () => void): void {
    const window = this.document.defaultView;
    if (!window) return;
    // CodeMirror measures its resized viewport in an animation frame too. Restore
    // after that measurement, keeping the same visible position in both layouts.
    this.restoreFrame = window.requestAnimationFrame(() => {
      restore();
      this.restoreFrame = window.requestAnimationFrame(() => {
        this.restoreFrame = 0;
        restore();
      });
    });
  }
}
