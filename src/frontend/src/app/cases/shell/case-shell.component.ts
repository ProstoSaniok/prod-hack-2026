import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { filter } from 'rxjs';
import { SidebarComponent } from '../../ui-kit/sidebar/sidebar.component';
import { SidebarItem } from '../../ui-kit/sidebar/sidebar.types';
import { BackendService } from '../../api/backend.service';
import { formatProditsAmount, proditsUnit } from '../../shared/prodits-presentation';
import { CurrencyAmountComponent } from '../../shared/currency-amount.component';
import { LeavePageService } from '../../shared/leave-page.service';
import { WorkspaceHostComponent } from '../../shared/workspace-maximize/workspace-host.component';
import { autoRefresh } from '../../shared/auto-refresh';

@Component({
  selector: 'app-case-shell',
  standalone: true,
  imports: [
    RouterOutlet,
    RouterLink,
    TuiButton,
    SidebarComponent,
    CurrencyAmountComponent,
    WorkspaceHostComponent,
  ],
  templateUrl: './case-shell.component.html',
  styleUrls: ['../case-theme.css', '../../shared/material-theme.css', './case-shell.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CaseShellComponent {
  readonly api = inject(BackendService);
  readonly logoutError = signal('');
  readonly loggingOut = signal(false);
  private readonly router = inject(Router);
  private readonly departure = inject(LeavePageService);
  private readonly navigationEnd = toSignal(
    this.router.events.pipe(filter(event => event instanceof NavigationEnd)),
  );
  readonly isExpert = computed(() => this.api.canAssessReadiness());
  readonly expanded = signal(true);
  readonly mobileOpen = signal(false);
  readonly activeId = computed(() => {
    const url = this.navigationEnd()?.urlAfterRedirects ?? this.router.url;
    if (url.startsWith('/my-cases/completed') || url.startsWith('/submissions/')) return 'started';
    if (url.startsWith('/projects/')) return 'started';
    if (url.startsWith('/review-results/') || url.startsWith('/reviews')) return 'reviews';
    if (url.startsWith('/expert/applications')) return 'applications';
    if (url.startsWith('/expert')) return 'expert';
    if (url.startsWith('/reviewer-applications/'))
      return this.isExpert() ? 'applications' : 'profile';
    if (url.startsWith('/profile')) return 'profile';
    const work = /^\/cases\/([^/?]+)\/work(?:\?|$)/.exec(url);
    if (work) return 'started';
    return url.startsWith('/my-cases') ? 'started' : 'catalog';
  });
  readonly editorPage = computed(() =>
    /^\/(?:cases\/[^/?#]+\/work|projects\/[^/?#]+)(?:[?#]|$)/.test(
      this.navigationEnd()?.urlAfterRedirects ?? this.router.url,
    ),
  );
  private readonly participantNavigation: readonly SidebarItem[] = [
    {
      id: 'catalog',
      label: 'Каталог кейсов',
      href: '/catalog',
      icon: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    },
    {
      id: 'started',
      label: 'В работе',
      href: '/my-cases',
      icon: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z',
    },
    {
      id: 'reviews',
      label: 'Ревью',
      href: '/reviews',
      icon: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
    },
  ];
  private readonly expertNavigation: SidebarItem = {
    id: 'expert',
    label: 'Экспертное ревью',
    href: '/expert',
    icon: 'm8 12 3 3 9-10 M21 12a9 9 0 1 1-6-8.5',
  };
  private readonly applicationsNavigation: SidebarItem = {
    id: 'applications',
    label: 'Заявки на эксперта',
    href: '/expert/applications',
    icon: 'M8 3h8v4H8z M8 5H5v16h14V5h-3 M8 12h8 M8 16h5',
  };
  readonly navigation = computed<readonly SidebarItem[]>(() =>
    this.api.isOrganizer()
      ? [
          {
            id: 'admin',
            label: 'Кейсы организатора',
            href: '/admin',
            icon: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
          },
          ...this.participantNavigation.filter(
            item => item.id === 'reviews' && this.api.hasOrdinaryAssignments(),
          ),
          ...(this.api.canOpenQualityReviews() ? [this.expertNavigation] : []),
          ...(this.api.canAssessReadiness() ? [this.applicationsNavigation] : []),
        ]
      : [
          ...this.participantNavigation.filter(
            item => item.id !== 'reviews' || this.api.canOpenReviews(),
          ),
          ...(this.api.canOpenQualityReviews() ? [this.expertNavigation] : []),
          ...(this.api.canAssessReadiness() ? [this.applicationsNavigation] : []),
        ],
  );
  readonly creditsBalance = computed(() => this.api.wallet()?.available ?? null);
  readonly formattedCredits = computed(() => formatProditsAmount(this.creditsBalance()));
  readonly creditsUnit = computed(() => proditsUnit(this.creditsBalance()));

  constructor() {
    autoRefresh(() => this.api.refreshReviewAccess(), {
      blocked: () => this.loggingOut(),
      shouldPoll: () => !!this.api.user(),
    });
  }

  async logout(): Promise<void> {
    if (this.loggingOut()) return;
    this.loggingOut.set(true);
    this.logoutError.set('');
    this.mobileOpen.set(false);
    try {
      if (!(await this.departure.confirmLeave())) return;
      if (this.api.user()) await this.api.logout();
      this.departure.approveNextNavigation();
      await this.router.navigate(['/login']);
    } catch (error) {
      this.logoutError.set(this.api.errorMessage(error));
    } finally {
      this.departure.clearApproval();
      this.loggingOut.set(false);
    }
  }
}
