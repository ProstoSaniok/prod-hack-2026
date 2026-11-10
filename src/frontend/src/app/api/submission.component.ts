import { LoadingStateComponent } from '../shared/loading-state.component';
import { DatePipe } from '@angular/common';
import { autoRefresh } from '../shared/auto-refresh';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { LearningService, Submission } from './learning.service';
import { ReviewFeedbackComponent } from './review-feedback.component';
import { SnapshotViewerComponent } from './snapshot-viewer.component';
import { projectStatusLabel } from './case-presentation';
import { SubmissionPriorityComponent } from './submission-priority.component';
@Component({
  standalone: true,
  imports: [
    LoadingStateComponent,
    DatePipe,
    RouterLink,
    TuiButton,
    ReviewFeedbackComponent,
    SnapshotViewerComponent,
    SubmissionPriorityComponent,
  ],
  template: `
    <div class="page-intro">
      <a routerLink="/my-cases">В работе</a>
      <h1>{{ title() || 'Отправленное решение' }}</h1>
    </div>
    @if (error()) {
      <p role="alert">{{ error() }}</p>
      <button
        tuiButton
        appearance="secondary"
        type="button"
        [disabled]="loading()"
        (click)="load()"
      >
        Повторить загрузку
      </button>
    }
    @if (loading()) {
      <app-loading-state label="Загружаем решение…" />
    }
    @if (submission(); as item) {
      <p class="submission-meta">
        <span role="status">{{ statusLabel(feedbackStatus() || item.status) }}</span>
        <span>Отправлено {{ item.submitted_at | date: 'dd.MM.yyyy HH:mm' }}</span>
      </p>
      @if (item.summary) {
        <p>{{ item.summary }}</p>
      }
      <app-review-feedback
        [submissionId]="item.id"
        [caseId]="item.case_id"
        (resultStatus)="feedbackStatus.set($event)"
      />
      <app-submission-priority [submission]="item" (refreshRequested)="refreshPriority()" />
      <app-snapshot-viewer [id]="item.id" />
    }
  `,
  styles: [
    `
      :host {
        display: block;
        min-width: 0;
        overflow-wrap: anywhere;
      }
      h1,
      p {
        margin: 16px 0;
      }
      p {
        line-height: 1.5;
      }
      .page-intro {
        padding-inline-start: var(--prodguild-page-heading-inset, 0);
        min-height: 44px;
      }
      .submission-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 24px;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SubmissionComponent {
  readonly service = inject(LearningService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly submission = signal<Submission | null>(null);
  readonly title = signal('');
  readonly feedbackStatus = signal('');
  readonly statusLabel = projectStatusLabel;
  readonly loading = signal(false);
  readonly error = signal('');
  private generation = 0;
  constructor() {
    autoRefresh(() => this.refreshPriority(), {
      blocked: () => this.loading(),
      shouldPoll: () => this.submission()?.status === 'submitted',
    });
    effect(() => {
      this.params();
      untracked(() => void this.load());
    });
  }
  async load(): Promise<void> {
    const generation = ++this.generation;
    this.loading.set(true);
    this.error.set('');
    this.submission.set(null);
    this.title.set('');
    this.feedbackStatus.set('');
    try {
      const item = await this.service.submission(this.params().get('id') ?? '');
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      this.submission.set(item);
      // A case can become unavailable after submission; the owner's result remains readable.
      const study = await this.service.case(item.case_id).catch(() => null);
      if (!this.destroyRef.destroyed && generation === this.generation && study)
        this.title.set(study.title);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.loading.set(false);
    }
  }
  async refreshPriority(): Promise<void> {
    const current = this.submission();
    if (!current) return;
    const generation = this.generation;
    try {
      const updated = await this.service.submission(current.id);
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.submission.set(updated);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.service.message(error));
    }
  }
}
