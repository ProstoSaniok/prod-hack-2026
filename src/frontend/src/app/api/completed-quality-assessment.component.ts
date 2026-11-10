import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { Assignment, QualityAssessmentRecord, ReviewService } from './review.service';

@Component({
  selector: 'app-completed-quality-assessment',
  standalone: true,
  imports: [DatePipe, TuiButton, LoadingStateComponent],
  template: `
    <section aria-label="Сохранённая экспертная оценка">
      <h2>Экспертная проверка завершена</h2>
      @if (loading()) {
        <app-loading-state label="Загружаем сохранённую оценку…" />
      }
      @if (error()) {
        <p role="alert">{{ error() }}</p>
        <button
          tuiButton
          appearance="secondary"
          type="button"
          [disabled]="loading()"
          (click)="load(offset())"
        >
          Повторить загрузку оценки
        </button>
      }
      @if (selected(); as assessment) {
        <p role="status">
          {{
            assessment.decision === 'confirmed'
              ? 'Ревью подтверждено'
              : 'Ревью возвращено на доработку'
          }}
        </p>
        <p>Оценка качества: {{ assessment.score }} / 100</p>
        <p>Сохранено {{ assessment.created_at | date: 'dd.MM.yyyy HH:mm' }}</p>
        <h3>Ваш комментарий ревьюеру</h3>
        <p class="feedback">{{ assessment.feedback }}</p>
      } @else if (!loading() && !error()) {
        <p role="status">Сохранённая оценка для этой проверки пока не найдена.</p>
        @if (next(); as nextOffset) {
          <button tuiButton appearance="secondary" type="button" (click)="load(nextOffset)">
            Продолжить поиск оценки
          </button>
        }
      }
    </section>
  `,
  styles: `
    :host {
      display: block;
      min-width: 0;
      margin-block: 24px;
    }
    section {
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
      overflow-wrap: anywhere;
    }
    .feedback {
      white-space: pre-wrap;
    }
    button {
      max-width: 100%;
      white-space: normal;
    }
    [role='alert'] {
      color: var(--tui-text-negative);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CompletedQualityAssessmentComponent {
  readonly assignment = input.required<Assignment>();
  private readonly api = inject(ReviewService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly actorId = computed(() => this.api.learning.api.user()?.id ?? '');
  readonly selected = signal<(QualityAssessmentRecord & { feedback: string }) | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly offset = signal(0);
  readonly next = signal<number | null>(null);
  private generation = 0;

  constructor() {
    effect(() => {
      this.assignment();
      this.actorId();
      untracked(() => {
        this.selected.set(null);
        void this.load(0);
      });
    });
  }

  private matches(record: QualityAssessmentRecord, task: Assignment, actor: string): boolean {
    return (
      record.assignment_id === task.id &&
      record.review_id === task.review_id &&
      record.revision_id === task.revision_id &&
      record.assessor_id === actor
    );
  }

  async load(offset: number): Promise<void> {
    const generation = ++this.generation;
    const task = this.assignment();
    const actor = this.actorId();
    const current = () =>
      !this.destroyRef.destroyed && generation === this.generation && actor === this.actorId();
    this.loading.set(true);
    this.error.set('');
    this.offset.set(offset);
    this.next.set(null);
    try {
      if (
        !actor ||
        task.kind !== 'quality_check' ||
        task.status !== 'completed' ||
        !task.review_id ||
        !task.revision_id
      )
        throw new Error('Сохранённая экспертная оценка недоступна для этого назначения.');
      const page = await this.api.assessmentRecords(task.review_id, offset);
      if (!current()) return;
      if (
        page.next_offset !== null &&
        (!Number.isInteger(page.next_offset) || page.next_offset <= offset)
      )
        throw new Error('Не удалось загрузить историю оценки. Повторите загрузку.');
      const matches = page.items.filter(item => this.matches(item, task, actor));
      if (matches.length > 1)
        throw new Error('Сервер вернул несколько оценок для одного назначения.');
      const match = matches[0];
      if (!match) {
        this.next.set(page.next_offset);
        return;
      }
      const result = await this.api.assessmentRecord(task.review_id, match.id);
      if (!current()) return;
      if (
        result.id !== match.id ||
        !this.matches(result, task, actor) ||
        !['confirmed', 'needs_revision'].includes(result.decision)
      )
        throw new Error('Сервер вернул оценку другой проверки. Повторите загрузку.');
      this.selected.set(result);
    } catch (error) {
      if (current()) this.error.set(this.api.message(error));
    } finally {
      if (current()) this.loading.set(false);
    }
  }
}
