import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  ViewEncapsulation,
  inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router } from '@angular/router';
import { WorkspaceMaximizeService } from './workspace-maximize.service';

@Component({
  selector: 'app-workspace-host',
  standalone: true,
  providers: [WorkspaceMaximizeService],
  template: '<ng-content />',
  styleUrl: './workspace-maximize.css',
  encapsulation: ViewEncapsulation.None,
  host: {
    'data-workspace-host': '',
    '[class.code-maximized]': '!!workspace.target()',
  },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorkspaceHostComponent {
  readonly workspace = inject(WorkspaceMaximizeService);
  constructor() {
    this.workspace.connect(inject<ElementRef<HTMLElement>>(ElementRef).nativeElement);
    inject(DestroyRef).onDestroy(() => this.workspace.close(false));
    inject(Router)
      .events.pipe(takeUntilDestroyed())
      .subscribe(event => {
        // A rejected dirty guard keeps both the route and the expanded workspace intact.
        if (event instanceof NavigationEnd) this.workspace.close(false);
      });
  }
}
