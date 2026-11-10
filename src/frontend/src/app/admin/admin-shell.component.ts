import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  inject,
  Injector,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';

import { BackendService } from '../api/backend.service';
import { SidebarComponent } from '../ui-kit/sidebar/sidebar.component';
import { SidebarItem } from '../ui-kit/sidebar/sidebar.types';
import { WorkspaceHostComponent } from '../shared/workspace-maximize/workspace-host.component';

@Component({
  selector: 'app-admin-shell',
  standalone: true,
  imports: [RouterOutlet, SidebarComponent, WorkspaceHostComponent],
  templateUrl: './admin-shell.component.html',
  styleUrls: ['../shared/material-theme.css', './admin-shell.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminShellComponent {
  private readonly api = inject(BackendService);
  private readonly injector = inject(Injector);
  private readonly content = viewChild.required<ElementRef<HTMLElement>>('content');
  readonly expanded = signal(true);
  readonly mobileOpen = signal(false);
  readonly navigation = computed<readonly SidebarItem[]>(() => {
    const user = this.api.user();
    const ownOrganizerSession =
      !!user && this.api.isOrganizer() && user.id === this.api.adminUserId();

    return [
      {
        id: 'cases',
        label: 'Кейсы',
        href: '/admin',
        icon: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z',
      },
      ...(ownOrganizerSession && this.api.canOpenQualityReviews()
        ? [
            {
              id: 'expert',
              label: 'Экспертное ревью',
              href: '/expert',
              icon: 'm8 12 3 3 9-10 M21 12a9 9 0 1 1-6-8.5',
            },
          ]
        : []),
      ...(ownOrganizerSession && this.api.canAssessReadiness()
        ? [
            {
              id: 'applications',
              label: 'Заявки на эксперта',
              href: '/expert/applications',
              icon: 'M8 3h8v4H8z M8 5H5v16h14V5h-3 M8 12h8 M8 16h5',
            },
          ]
        : []),
    ];
  });
  readonly footerNavigation: readonly SidebarItem[] = [
    {
      id: 'exit',
      label: 'К странице входа',
      href: '/login',
      icon: 'M9 5H4v14h5 M10 12h11 m-4-4 4 4-4 4',
    },
  ];

  private previousPath = '';

  constructor() {
    inject(Router)
      .events.pipe(
        filter(event => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(event => {
        const path = event.urlAfterRedirects.split('?')[0];
        const preserveEditor =
          path === this.previousPath ||
          (this.previousPath === '/admin/new' && /^\/admin\/[^/]+\/edit$/.test(path));
        this.previousPath = path;
        this.mobileOpen.set(false);
        if (!preserveEditor) this.focusHeading();
      });
  }

  onNavigate(item: SidebarItem): void {
    if (item.id === 'cases') this.focusHeading();
  }

  private focusHeading(): void {
    afterNextRender(() => this.content().nativeElement.querySelector('h1')?.focus(), {
      injector: this.injector,
    });
  }
}
