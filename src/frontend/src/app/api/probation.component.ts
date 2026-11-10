import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import { Probation, ReviewService } from './review.service';
import { CompetencyCatalogService } from './competency-catalog.service';
import { autoRefresh } from '../shared/auto-refresh';
import { CompetencyNamesNoticeComponent } from '../shared/competency-names-notice.component';
@Component({
  selector: 'app-probation',
  standalone: true,
  imports: [TuiButton, CompetencyNamesNoticeComponent],
  template: `
    <h2>Испытательный период ревьюера</h2>
    @if (error()) {
      <p role="alert">{{ error() }}</p>
    }
    @for (item of items(); track item.id) {
      <p>
        {{ skills().get(item.competency_id) ?? 'Компетенция' }} · {{ grade(item.grade) }} ·
        Подтверждено {{ item.confirmed_reviews }} из {{ item.required_successes }}
      </p>
    } @empty {
      @if (!busy() && !error()) {
        <p>Испытательных периодов нет.</p>
      }
    }
    <app-competency-names-notice [skills]="items()" />
    @if (offset() > 0 || next() !== null) {
      <button
        tuiButton
        appearance="flat"
        type="button"
        [disabled]="busy() || offset() === 0"
        (click)="load(offset() - 20)"
      >
        Предыдущие навыки</button
      ><button
        tuiButton
        appearance="flat"
        type="button"
        [disabled]="busy() || next() === null"
        (click)="load(next() ?? 0)"
      >
        Следующие навыки
      </button>
    }
    @if (error()) {
      <button
        tuiButton
        appearance="secondary"
        type="button"
        [disabled]="busy()"
        (click)="load(offset())"
      >
        Повторить загрузку испытательного периода
      </button>
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProbationComponent {
  readonly api = inject(ReviewService);
  private readonly competencyCatalog = inject(CompetencyCatalogService);
  private readonly destroyRef = inject(DestroyRef);
  readonly items = signal<Probation[]>([]);
  readonly skills = this.competencyCatalog.names;
  readonly offset = signal(0);
  readonly next = signal<number | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  constructor() {
    void this.competencyCatalog.ensureLoaded();
    autoRefresh(() => this.load(this.offset()), {
      blocked: () => this.busy(),
      shouldPoll: () => this.items().some(item => item.remaining > 0),
    });
    void this.load(0);
  }
  grade(value: string): string {
    return value === 'review_plus'
      ? 'Ревьюер+'
      : value === 'review_minus'
        ? 'Ревьюер−'
        : value === 'expert'
          ? 'Эксперт'
          : 'Исполнитель';
  }
  async load(offset: number): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      const page = await this.api.probation(offset);
      if (this.destroyRef.destroyed) return;
      this.items.set(page.items);
      this.offset.set(offset);
      this.next.set(page.next_offset);
    } catch (error) {
      if (!this.destroyRef.destroyed) this.error.set(this.api.message(error));
    } finally {
      if (!this.destroyRef.destroyed) this.busy.set(false);
    }
  }
}
