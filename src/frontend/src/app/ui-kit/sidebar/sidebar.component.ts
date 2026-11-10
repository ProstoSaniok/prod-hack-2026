import { CdkTrapFocus } from '@angular/cdk/a11y';
import { BreakpointObserver } from '@angular/cdk/layout';
import {
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  computed,
  DOCUMENT,
  effect,
  ElementRef,
  inject,
  input,
  model,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';

import { SidebarItem } from './sidebar.types';

let nextSidebarId = 0;

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [CdkTrapFocus, RouterLink, TuiButton],
  templateUrl: './sidebar.component.html',
  styleUrl: './sidebar.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '[class.expanded]': 'expanded()',
    '[class.compact]': '!expanded() && !isMobile()',
    '[class.desktop-motion]': 'motionEnabled() && !isMobile()',
    '[class.mobile-motion]': 'motionEnabled() && isMobile()',
    '[attr.data-expanded]': 'panelOpen()',
  },
})
export class SidebarComponent {
  readonly items = input.required<readonly SidebarItem[]>();
  readonly footerItems = input<readonly SidebarItem[]>([]);
  readonly activeId = input('');
  readonly workspaceName = input<string>();
  readonly expanded = model(false);
  readonly mobileOpen = model(false);
  readonly motionEnabled = signal(false);
  readonly navigate = output<SidebarItem>();
  readonly panelId = `sidebar-panel-${nextSidebarId++}`;

  private readonly document = inject(DOCUMENT);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly trap = viewChild.required(CdkTrapFocus);
  private readonly toggleButton = viewChild.required<ElementRef<HTMLButtonElement>>('toggle');
  private readonly panel = viewChild.required<ElementRef<HTMLElement>>('panel');
  private readonly viewport = toSignal(inject(BreakpointObserver).observe('(max-width: 620px)'));
  private mobileOpener: Element | null = null;

  readonly isMobile = computed(() => this.viewport()?.matches ?? false);
  readonly modalOpen = computed(() => this.isMobile() && this.mobileOpen());
  readonly panelOpen = computed(() => (this.isMobile() ? this.mobileOpen() : this.expanded()));
  readonly panelVisible = computed(() => !this.isMobile() || this.mobileOpen());
  readonly toggleLabel = computed(() =>
    this.panelOpen() ? 'Скрыть боковую панель' : 'Показать боковую панель',
  );

  constructor() {
    effect(() => {
      const mobile = this.isMobile();
      this.motionEnabled.set(false);
      if (!mobile) this.mobileOpen.set(false);
    });

    afterRenderEffect(onCleanup => {
      if (
        !this.panelVisible() &&
        this.panel().nativeElement.contains(this.document.activeElement)
      ) {
        this.toggleButton().nativeElement.focus({ preventScroll: true });
      }
      if (!this.modalOpen()) return;

      const opener = this.mobileOpener ?? this.document.activeElement;
      const restoreBackground = this.lockBackground();
      void this.trap().focusTrap.focusInitialElementWhenReady();

      onCleanup(() => {
        restoreBackground();
        this.mobileOpener = null;
        if (
          opener instanceof HTMLElement &&
          opener !== this.document.body &&
          opener.isConnected &&
          opener.getClientRects().length > 0 &&
          !opener.closest('[inert]')
        ) {
          opener.focus({ preventScroll: true });
        } else if (this.host.nativeElement.isConnected) {
          this.toggleButton().nativeElement.focus({ preventScroll: true });
        }
      });
    });
  }

  togglePanel(): void {
    this.motionEnabled.set(true);
    if (this.isMobile()) {
      if (!this.mobileOpen()) this.mobileOpener = this.document.activeElement;
      this.mobileOpen.set(!this.mobileOpen());
    } else {
      this.expanded.set(!this.expanded());
    }
  }

  closeMobile(): void {
    this.mobileOpen.set(false);
  }

  select(item: SidebarItem): void {
    this.navigate.emit(item);
    this.closeMobile();
  }

  /** Make each background branch inert without imposing a shell structure on consumers. */
  private lockBackground(): () => void {
    const previous = new Map<HTMLElement, boolean>();
    let branch: HTMLElement | null = this.host.nativeElement;

    while (branch && branch !== this.document.body) {
      const parent: HTMLElement | null = branch.parentElement;
      if (!parent) break;
      for (const sibling of Array.from(parent.children)) {
        if (sibling !== branch && sibling instanceof HTMLElement) {
          previous.set(sibling, sibling.inert);
          sibling.inert = true;
        }
      }
      branch = parent;
    }

    const overflow = this.document.body.style.overflow;
    this.document.body.style.overflow = 'hidden';
    return () => {
      previous.forEach((inert, element) => (element.inert = inert));
      this.document.body.style.overflow = overflow;
    };
  }
}
