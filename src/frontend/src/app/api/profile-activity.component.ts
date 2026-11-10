import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { CurrencyAmountComponent } from '../shared/currency-amount.component';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { Activity, BackendService } from './backend.service';
import { DevelopmentService } from './development.service';
import { PriorityRequest } from './learning.service';
import { ReviewerApplicationsComponent } from './reviewer-applications.component';
import { autoRefresh } from '../shared/auto-refresh';

@Component({
  selector: 'app-profile-activity',
  standalone: true,
  imports: [
    DatePipe,
    RouterLink,
    TuiButton,
    LoadingStateComponent,
    CurrencyAmountComponent,
    ReviewerApplicationsComponent,
    ProductIconComponent,
  ],
  template: `
    <section id="activity" class="workflow-panel" aria-label="История развития">
      <h2>История</h2>
      <nav class="actions" aria-label="Разделы истории">
        <button
          tuiButton
          size="s"
          type="button"
          [appearance]="tab() === 'wallet' ? 'accent' : 'secondary'"
          [attr.aria-pressed]="tab() === 'wallet'"
          (click)="select('wallet')"
        >
          Операции
        </button>
        <button
          tuiButton
          size="s"
          type="button"
          [appearance]="tab() === 'activity' ? 'accent' : 'secondary'"
          [attr.aria-pressed]="tab() === 'activity'"
          (click)="select('activity')"
        >
          Активность
        </button>
        <button
          tuiButton
          size="s"
          type="button"
          [appearance]="tab() === 'priority' ? 'accent' : 'secondary'"
          [attr.aria-pressed]="tab() === 'priority'"
          (click)="select('priority')"
        >
          Приоритет проверок
        </button>
        <button
          tuiButton
          size="s"
          type="button"
          [appearance]="tab() === 'applications' ? 'accent' : 'secondary'"
          [attr.aria-pressed]="tab() === 'applications'"
          (click)="select('applications')"
        >
          Заявки
        </button>
      </nav>
      <div [hidden]="tab() !== 'wallet'"><ng-content /></div>
      @if (applicationsOpened()) {
        <app-reviewer-applications mode="owner" [hidden]="tab() !== 'applications'" />
      }
      @if (tab() === 'activity') {
        @if (activityLoading()) {
          <app-loading-state label="Загружаем активность…" />
        }
        @if (activityError()) {
          <p class="error" role="alert">{{ activityError() }}</p>
        }
        <ul class="workflow-list" aria-label="Активность">
          @for (item of activity(); track item.id) {
            <li class="workflow-row">
              <span>
                @if (activityPath(item); as path) {
                  <a [routerLink]="path"
                    >{{ item.description }} <app-product-icon name="arrow-right"
                  /></a>
                } @else {
                  {{ item.description }}
                }
                <time [attr.datetime]="item.created_at">{{
                  item.created_at | date: 'dd.MM.yyyy HH:mm'
                }}</time>
              </span>
            </li>
          } @empty {
            @if (!activityLoading() && !activityError()) {
              <li class="empty-state">Событий пока нет.</li>
            }
          }
        </ul>
        @if (activityError()) {
          <button
            tuiButton
            appearance="secondary"
            type="button"
            [disabled]="activityLoading()"
            (click)="loadActivity()"
          >
            Повторить загрузку активности
          </button>
        }
        @if (activityOffset() > 0 || activity().length === 20) {
          <nav class="actions" aria-label="Страницы активности">
            <button
              tuiButton
              appearance="flat"
              type="button"
              [disabled]="activityLoading() || activityOffset() === 0"
              (click)="loadActivity(activityOffset() - 20)"
            >
              Предыдущие события</button
            ><span>Страница {{ activityOffset() / 20 + 1 }}</span
            ><button
              tuiButton
              appearance="flat"
              type="button"
              [disabled]="activityLoading() || activity().length < 20"
              (click)="loadActivity(activityOffset() + 20)"
            >
              Следующие события
            </button>
          </nav>
        }
      } @else if (tab() === 'priority') {
        @if (priorityLoading()) {
          <app-loading-state label="Загружаем историю приоритета…" />
        }
        @if (priorityError()) {
          <p class="error" role="alert">{{ priorityError() }}</p>
        }
        <ul class="workflow-list" aria-label="История приоритета">
          @for (item of priorities(); track item.id) {
            <li>
              <a class="workflow-row" [routerLink]="['/submissions', item.submission_id]"
                ><span
                  ><strong>{{ service.priorityStatus[item.status] }}</strong
                  ><time [attr.datetime]="item.created_at">{{
                    item.created_at | date: 'dd.MM.yyyy HH:mm'
                  }}</time></span
                ><span
                  ><app-currency-amount [value]="item.cost" />
                  <app-product-icon name="arrow-right" /></span
              ></a>
            </li>
          } @empty {
            @if (!priorityLoading() && !priorityError()) {
              <li class="empty-state">История приоритета пока пуста.</li>
            }
          }
        </ul>
        @if (priorityError()) {
          <button
            tuiButton
            appearance="flat"
            type="button"
            [disabled]="priorityLoading()"
            (click)="loadPriorities()"
          >
            Повторить загрузку истории приоритета
          </button>
        }
        @if (priorityOffset() > 0 || priorityNext() !== null) {
          <nav class="actions" aria-label="Страницы истории приоритета">
            <button
              tuiButton
              appearance="flat"
              type="button"
              [disabled]="priorityLoading() || priorityOffset() === 0"
              (click)="loadPriorities(priorityOffset() - 20)"
            >
              Предыдущие запросы</button
            ><span>Страница {{ priorityOffset() / 20 + 1 }}</span
            ><button
              tuiButton
              appearance="flat"
              type="button"
              [disabled]="priorityLoading() || priorityNext() === null"
              (click)="loadPriorities(priorityNext() ?? priorityOffset())"
            >
              Следующие запросы
            </button>
          </nav>
        }
      }
    </section>
  `,
  styleUrl: './workflow-panels.css',
  styles: `
    [hidden] {
      display: none !important;
    }
    app-reviewer-applications {
      --workflow-panel-padding: 0;
      --workflow-panel-background: transparent;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProfileActivityComponent {
  readonly api = inject(BackendService);
  readonly service = inject(DevelopmentService);
  private readonly destroyRef = inject(DestroyRef);
  readonly fragment = input<string | null>(null);
  readonly tab = signal<'wallet' | 'activity' | 'priority' | 'applications'>('wallet');
  readonly applicationsOpened = signal(false);
  readonly activity = signal<Activity[]>([]);
  readonly activityOffset = signal(0);
  readonly activityLoading = signal(false);
  readonly activityError = signal('');
  private requestedActivityOffset = 0;
  readonly priorities = signal<PriorityRequest[]>([]);
  readonly priorityOffset = signal(0);
  readonly priorityNext = signal<number | null>(null);
  readonly priorityLoading = signal(false);
  readonly priorityError = signal('');
  private requestedPriorityOffset = 0;
  constructor() {
    autoRefresh(
      async () => {
        if (this.tab() === 'activity') await this.loadActivity();
        if (this.tab() === 'priority') await this.loadPriorities();
      },
      {
        blocked: () => this.activityLoading() || this.priorityLoading(),
        shouldPoll: () =>
          this.tab() === 'priority' && this.priorities().some(item => item.status === 'reserved'),
      },
    );
    effect(() => {
      const fragment = this.fragment();
      untracked(() =>
        this.select(
          fragment === 'history' || fragment === 'activity'
            ? 'activity'
            : fragment === 'priority'
              ? 'priority'
              : fragment === 'applications'
                ? 'applications'
                : 'wallet',
        ),
      );
    });
  }
  select(tab: 'wallet' | 'activity' | 'priority' | 'applications'): void {
    this.tab.set(tab);
    if (tab === 'applications') this.applicationsOpened.set(true);
    if (tab === 'activity' && !this.activity().length && !this.activityLoading())
      void this.loadActivity(0);
    if (tab === 'priority' && !this.priorities().length && !this.priorityLoading())
      void this.loadPriorities(0);
  }
  activityPath(item: Activity): string | null {
    const id = encodeURIComponent(item.resource_id);
    if (item.type === 'case_started') return `/projects/${id}`;
    if (item.type === 'solution_submitted') return `/submissions/${id}`;
    if (item.type === 'review_published') return `/review-results/${id}`;
    if (['readiness_requested', 'readiness_decided'].includes(item.type))
      return `/reviewer-applications/${id}`;
    return null;
  }
  async loadActivity(
    offset = this.activityError() ? this.requestedActivityOffset : this.activityOffset(),
  ): Promise<void> {
    if (this.activityLoading()) return;
    const owner = this.api.user()?.id;
    this.requestedActivityOffset = offset;
    this.activityLoading.set(true);
    this.activityError.set('');
    try {
      const rows = await this.api.activity(offset);
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner) {
        this.activity.set(rows);
        this.activityOffset.set(offset);
      }
    } catch (error) {
      if (!this.destroyRef.destroyed) this.activityError.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed) this.activityLoading.set(false);
    }
  }
  async loadPriorities(
    offset = this.priorityError() ? this.requestedPriorityOffset : this.priorityOffset(),
  ): Promise<void> {
    if (this.priorityLoading()) return;
    const owner = this.api.user()?.id;
    this.requestedPriorityOffset = offset;
    this.priorityLoading.set(true);
    this.priorityError.set('');
    try {
      const page = await this.service.priorities(offset);
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner) {
        this.priorities.set(page.items);
        this.priorityOffset.set(offset);
        this.priorityNext.set(page.next_offset);
      }
    } catch (error) {
      if (!this.destroyRef.destroyed) this.priorityError.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed) this.priorityLoading.set(false);
    }
  }
}
