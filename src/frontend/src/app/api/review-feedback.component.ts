import { autoRefresh } from '../shared/auto-refresh';
import { LoadingStateComponent } from '../shared/loading-state.component';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import { Contribution, Review, ReviewService } from './review.service';
import { CompetencyCatalogService } from './competency-catalog.service';
import { PublishedReviewComponent } from './published-review.component';
import { formatSkillValue } from './case-presentation';
import { CurrencyAmountComponent } from '../shared/currency-amount.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { CompetencyNamesNoticeComponent } from '../shared/competency-names-notice.component';
@Component({
  selector: 'app-review-feedback',
  standalone: true,
  imports: [
    LoadingStateComponent,
    TuiButton,
    PublishedReviewComponent,
    CurrencyAmountComponent,
    ProductIconComponent,
    CompetencyNamesNoticeComponent,
  ],
  template: `
    @if (error()) {
      <p role="alert">{{ error() }}</p>
    }
    @if (loading()) {
      <app-loading-state label="Проверяем результаты ревью…" />
    }
    @for (review of reviews(); track review.id) {
      <app-published-review [review]="review" />
    }
    @if (!loading() && !error() && reviews().length === 0) {
      <p class="waiting" role="status"><app-product-icon name="clock" />Ожидаем результат ревью.</p>
    }
    @if (reward(); as amount) {
      <p class="case-reward">
        За этот кейс получено: <app-currency-amount [value]="amount" [showPlus]="true" />
      </p>
    }
    @if (rewardError()) {
      <p role="alert">Не удалось загрузить награду.</p>
    }
    @if (progressionError()) {
      <p role="alert">Не удалось загрузить изменения навыков. {{ progressionError() }}</p>
    }
    @if (contributions().length) {
      <section aria-label="Изменение навыков">
        <h2>Навыки</h2>
        @for (item of contributions(); track item.competency_id) {
          <p>
            {{ skills().get(item.competency_id) ?? 'Навык' }}: +{{
              contribution(item.contribution)
            }}
            XP · {{ skillValue(item.displayed_value_before) }} →
            {{ skillValue(item.displayed_value_after) }} XP
          </p>
        }
        <app-competency-names-notice [skills]="contributions()" />
      </section>
    }
    @if (error() || progressionError() || rewardError()) {
      <button
        tuiButton
        appearance="secondary"
        type="button"
        [disabled]="loading()"
        (click)="load()"
      >
        <app-product-icon name="refresh" />
        Повторить загрузку результатов
      </button>
    }
  `,
  styles: `
    :host {
      display: grid;
      gap: 20px;
      min-width: 0;
      margin-top: 24px;
    }
    section,
    .waiting,
    .case-reward {
      padding: 20px;
      border-radius: var(--prodguild-radius-lg);
      background: var(--prodguild-surface-raised);
    }
    .waiting,
    .case-reward {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }
    h2,
    p {
      margin: 0;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    section p {
      margin-top: 12px;
    }
    button {
      justify-self: start;
    }
    [role='alert'] {
      color: var(--tui-text-negative);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewFeedbackComponent {
  readonly submissionId = input.required<string>();
  readonly caseId = input.required<string>();
  readonly resultStatus = output<string>();
  readonly skillValue = formatSkillValue;
  readonly contribution = (value: number): string =>
    value.toLocaleString('ru-RU', { maximumFractionDigits: 20 });
  readonly api = inject(ReviewService);
  private readonly competencyCatalog = inject(CompetencyCatalogService);
  private readonly destroyRef = inject(DestroyRef);
  readonly reviews = signal<Review[]>([]);
  readonly contributions = signal<Contribution[]>([]);
  readonly skills = this.competencyCatalog.names;
  readonly loading = signal(false);
  readonly error = signal('');
  readonly progressionError = signal('');
  readonly reward = signal<number | null>(null);
  readonly rewardError = signal(false);
  private generation = 0;
  constructor() {
    void this.competencyCatalog.ensureLoaded();
    autoRefresh(() => this.load(), {
      blocked: () => this.loading(),
      shouldPoll: () => this.reviews().length === 0,
    });
    effect(() => {
      this.submissionId();
      this.caseId();
      this.reviews.set([]);
      this.contributions.set([]);
      this.reward.set(null);
      untracked(() => void this.load());
    });
  }
  async load(): Promise<void> {
    const generation = ++this.generation;
    this.loading.set(true);
    this.error.set('');
    this.progressionError.set('');
    this.rewardError.set(false);
    try {
      await Promise.all([this.loadFeedback(generation), this.loadProgression(generation)]);
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      await Promise.all([
        this.api.learning.api.refreshWallet(),
        this.api.learning.api.refreshReviewAccess(),
      ]);
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.loading.set(false);
    }
  }
  private async loadFeedback(generation: number): Promise<void> {
    try {
      const reviews = await this.api.feedback(this.submissionId());
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      this.reviews.set(reviews);
      this.resultStatus.emit(reviews[0]?.current_revision.verdict ?? '');
      if (
        reviews.some(
          review => review.status === 'published' && review.current_revision.verdict === 'approved',
        )
      )
        await this.loadReward(generation);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.api.message(error));
    }
  }
  private async loadProgression(generation: number): Promise<void> {
    try {
      const contributions = await this.api.progression(this.submissionId());
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      this.contributions.set(contributions);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.progressionError.set(this.api.message(error));
    }
  }
  private async loadReward(generation: number): Promise<void> {
    const source = `successful_case:${this.caseId()}:${this.api.learning.api.user()?.id}`;
    try {
      for (let offset = 0; ; offset += 20) {
        const page = await this.api.learning.api.transactions(offset);
        if (this.destroyRef.destroyed || generation !== this.generation) return;
        const reward = page.find(
          item =>
            item.kind === 'case_reward' && item.source_id === source && item.settled_delta > 0,
        );
        if (reward) {
          this.reward.set(reward.settled_delta);
          return;
        }
        if (page.length < 20) return;
      }
    } catch {
      if (!this.destroyRef.destroyed && generation === this.generation) this.rewardError.set(true);
    }
  }
}
