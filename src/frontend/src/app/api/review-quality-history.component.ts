import { LoadingStateComponent } from '../shared/loading-state.component';
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
import { TuiButton } from '@taiga-ui/core';
import { QualityAssessment, Review, ReviewService } from './review.service';
import { PublishedReviewComponent } from './published-review.component';
@Component({
  selector: 'app-review-quality-history',
  standalone: true,
  imports: [LoadingStateComponent, TuiButton, PublishedReviewComponent],
  template: `
    <h2>Экспертные проверки моего ревью</h2>
    @if (error()) {
      <p role="alert">{{ error() }}</p>
      <button
        tuiButton
        appearance="secondary"
        type="button"
        [disabled]="busy()"
        (click)="load(offset())"
      >
        Повторить загрузку оценок
      </button>
    }
    @if (busy()) {
      <app-loading-state label="Загружаем экспертные оценки…" />
    }
    @for (assessment of assessments(); track assessment.id) {
      <p>
        <button
          tuiButton
          appearance="flat"
          type="button"
          [disabled]="busy()"
          [attr.aria-pressed]="selected()?.id === assessment.id"
          (click)="open(assessment.id)"
        >
          {{ assessment.decision === 'confirmed' ? 'Подтверждено' : 'Нужна доработка' }} ·
          {{ assessment.score }}
        </button>
      </p>
    } @empty {
      @if (!busy() && !error()) {
        <p>Экспертных оценок пока нет.</p>
      }
    }
    <button
      tuiButton
      appearance="flat"
      type="button"
      [disabled]="busy() || offset() === 0"
      (click)="load(offset() - 20)"
    >
      Предыдущие оценки</button
    ><button
      tuiButton
      appearance="flat"
      type="button"
      [disabled]="busy() || next() === null"
      (click)="load(next() ?? 0)"
    >
      Следующие оценки
    </button>
    @if (selected(); as assessment) {
      <h3>Приватный комментарий эксперта</h3>
      <p>
        {{ assessment.decision === 'confirmed' ? 'Подтверждено' : 'Нужна доработка' }} · Оценка
        качества: {{ assessment.score }} / 100
      </p>
      <p>{{ assessment.feedback }}</p>
      <button
        tuiButton
        appearance="flat"
        type="button"
        [disabled]="busy()"
        (click)="revision(assessment.revision_id)"
      >
        Открыть оценённую редакцию
      </button>
    }
    @if (historical(); as review) {
      @if (review.current_revision.id === selected()?.revision_id) {
        <h3>Редакция, к которой относится экспертная оценка</h3>
        <app-published-review [review]="review" [historical]="true" />
      }
    }
  `,
  styles: `
    :host {
      display: block;
      min-width: 0;
      margin-block: 24px;
      padding: clamp(16px, 2.5vw, 28px);
      border-radius: var(--prodguild-radius-lg);
      background: var(--prodguild-surface);
    }
    h2 {
      margin: 0 0 16px;
      font-size: var(--prodguild-font-section);
    }
    h3,
    p {
      margin: 16px 0;
      line-height: 1.5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    button {
      margin: 0 8px 8px 0;
      max-width: 100%;
      white-space: normal;
    }
    [role='alert'] {
      color: var(--tui-text-negative);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewQualityHistoryComponent {
  readonly reviewId = input.required<string>();
  readonly api = inject(ReviewService);
  private readonly destroyRef = inject(DestroyRef);
  readonly assessments = signal<QualityAssessment[]>([]);
  readonly selected = signal<QualityAssessment | null>(null);
  readonly historical = signal<Review | null>(null);
  readonly offset = signal(0);
  readonly next = signal<number | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  private generation = 0;
  constructor() {
    effect(() => {
      this.reviewId();
      untracked(() => void this.load(0));
    });
  }
  private async run(action: () => Promise<() => void>): Promise<void> {
    if (this.busy()) return;
    const generation = this.generation;
    this.busy.set(true);
    this.error.set('');
    try {
      const update = await action();
      if (!this.destroyRef.destroyed && generation === this.generation) update();
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.api.message(error));
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.busy.set(false);
    }
  }
  async load(offset: number): Promise<void> {
    ++this.generation;
    this.busy.set(false);
    this.assessments.set([]);
    this.selected.set(null);
    this.historical.set(null);
    this.offset.set(offset);
    this.next.set(null);
    const reviewId = this.reviewId();
    await this.run(async () => {
      const page = await this.api.assessments(reviewId, offset);
      return () => {
        this.assessments.set(page.items);
        this.next.set(page.next_offset);
      };
    });
  }
  async open(id: string): Promise<void> {
    if (this.busy()) return;
    this.selected.set(null);
    this.historical.set(null);
    const reviewId = this.reviewId();
    await this.run(async () => {
      const selected = await this.api.assessment(reviewId, id);
      return () => this.selected.set(selected);
    });
  }
  async revision(id: string): Promise<void> {
    if (this.busy() || this.selected()?.revision_id !== id) return;
    this.historical.set(null);
    const reviewId = this.reviewId();
    await this.run(async () => {
      const revision = await this.api.revision(reviewId, id);
      if (revision.id !== reviewId || revision.current_revision.id !== id)
        throw new Error('Сервер вернул другую редакцию ревью. Повторите загрузку.');
      return () => this.historical.set(revision);
    });
  }
}
