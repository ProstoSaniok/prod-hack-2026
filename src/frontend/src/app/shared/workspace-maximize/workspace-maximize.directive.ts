import { DestroyRef, Directive, ElementRef, computed, inject } from '@angular/core';
import { WorkspaceMaximizeService } from './workspace-maximize.service';

let nextWorkspaceId = 0;

@Directive({
  selector: '[appWorkspaceMaximize]',
  standalone: true,
  exportAs: 'workspaceMaximize',
  host: { '[id]': 'id', '(keydown.escape)': 'escape($event)' },
})
export class WorkspaceMaximizeDirective {
  readonly id = `prodguild-code-workspace-${nextWorkspaceId++}`;
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly workspace = inject(WorkspaceMaximizeService, { optional: true });
  readonly available = !!this.workspace;
  readonly expanded = computed(() => this.workspace?.target() === this.host);

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      if (this.expanded()) this.workspace?.close(false);
    });
  }

  toggle(opener: HTMLElement): void {
    if (this.expanded()) this.workspace?.close();
    else this.workspace?.open(this.host, opener);
  }

  escape(event: KeyboardEvent): void {
    if (!this.expanded() || event.defaultPrevented) return;
    event.preventDefault();
    this.workspace?.close();
  }
}
