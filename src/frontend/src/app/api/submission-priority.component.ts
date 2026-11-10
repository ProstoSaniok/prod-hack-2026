import { autoRefresh } from '../shared/auto-refresh';
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
import { CurrencyAmountComponent } from '../shared/currency-amount.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { DevelopmentService } from './development.service';
import { Submission } from './learning.service';

@Component({
  selector: 'app-submission-priority',
  standalone: true,
  imports: [TuiButton, CurrencyAmountComponent, ProductIconComponent],
  template: `
    <section class="priority-panel" aria-labelledby="priority-title" [attr.aria-busy]="busy()">
      <h2 id="priority-title"><app-product-icon name="clock" /> Приоритет проверки</h2>
      @if (submission().priority; as priority) {
        @if (priority.current_request; as request) {
          <p role="status">
            {{ service.priorityStatus[request.status] }} ·
            <app-currency-amount [value]="request.cost" />
          </p>
        } @else {
          <p>Обычная очередь</p>
        }
        @if (
          priority.current_request?.status !== 'reserved' &&
          priority.current_request?.status !== 'settled'
        ) {
          <p>
            Стоимость: <app-currency-amount [value]="cost() ?? priority.cost" />. Средства
            резервируются до назначения ревьюера. Приоритет не гарантирует срок проверки.
          </p>
          @if (!priority.can_purchase) {
            <p role="status">
              {{
                (service.api.wallet()?.available ?? priority.cost) < priority.cost
                  ? 'Недостаточно средств для приоритета.'
                  : 'Приоритет сейчас недоступен для этого решения.'
              }}
            </p>
          }
        }
        @if (pending()) {
          <p role="status">
            Ответ на предыдущий запрос не получен. Повторите его, чтобы уточнить результат.
          </p>
          <button
            tuiButton
            appearance="secondary"
            type="button"
            [disabled]="busy()"
            (click)="act('retry')"
          >
            Повторить запрос приоритета
          </button>
        } @else if (confirming()) {
          <div class="confirmation">
            <p>
              Зарезервировать <app-currency-amount [value]="cost() ?? 0" /> для проверки этого
              решения?
            </p>
            <div class="actions">
              <button
                tuiButton
                appearance="accent"
                type="button"
                [disabled]="busy() || !priority.can_purchase || cost() !== priority.cost"
                (click)="act('purchase')"
              >
                Подтвердить приоритет
              </button>
              <button
                tuiButton
                appearance="flat"
                type="button"
                [disabled]="busy()"
                (click)="confirming.set(false)"
              >
                Отмена
              </button>
            </div>
          </div>
        } @else {
          <div class="actions">
            @if (priority.can_purchase) {
              <button
                tuiButton
                appearance="accent"
                type="button"
                [disabled]="busy() || cost() === null || cost() !== priority.cost"
                (click)="confirming.set(true)"
              >
                Повысить приоритет
              </button>
            }
            @if (priority.can_cancel) {
              <button
                tuiButton
                appearance="secondary"
                type="button"
                [disabled]="busy()"
                (click)="act('cancel')"
              >
                Отменить приоритет
              </button>
            }
          </div>
        }
      } @else {
        <p>Статус приоритета недоступен.</p>
      }
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      @if (service.mutations.storageNotice()) {
        <p role="status">{{ service.mutations.storageNotice() }}</p>
      }
      @if (error() || !submission().priority) {
        <button
          tuiButton
          appearance="secondary"
          type="button"
          [disabled]="busy()"
          (click)="refresh()"
        >
          <app-product-icon name="refresh" /> Повторить загрузку статуса
        </button>
      }
    </section>
  `,
  styles: `
    .priority-panel {
      margin: 24px 0;
      padding: clamp(16px, 3vw, 28px);
      border-radius: var(--prodguild-radius-lg);
      background: var(--prodguild-surface-raised);
    }
    h2 {
      display: flex;
      align-items: center;
      gap: 10px;
      margin: 0 0 16px;
      font-size: var(--prodguild-font-section);
    }
    p {
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .confirmation {
      padding: 16px;
      border-radius: var(--prodguild-radius-md);
      background: var(--prodguild-surface-muted);
    }
    .error {
      color: var(--tui-text-negative);
    }
    button {
      max-width: 100%;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SubmissionPriorityComponent {
  readonly submission = input.required<Submission>();
  readonly refreshRequested = output<void>();
  readonly service = inject(DevelopmentService);
  private readonly destroyRef = inject(DestroyRef);
  readonly busy = signal(false);
  readonly pending = signal(false);
  readonly confirming = signal(false);
  readonly cost = signal<number | null>(null);
  readonly error = signal('');
  constructor() {
    autoRefresh(() => this.refresh(), {
      blocked: () => this.busy() || this.confirming() || this.pending(),
      shouldPoll: () =>
        this.submission().priority?.current_request?.status === 'reserved' ||
        this.submission().priority?.current_request?.status === 'settled',
    });
    effect(() => {
      const id = this.submission().id;
      untracked(() =>
        this.pending.set(this.service.mutations.pending(this.service.priorityAction(id))),
      );
    });
    void this.loadPolicy();
  }
  private async loadPolicy(): Promise<void> {
    try {
      const policy = await this.service.api.developmentPolicy();
      if (!this.destroyRef.destroyed) this.cost.set(policy.priority_cost);
    } catch (error) {
      if (!this.destroyRef.destroyed) this.error.set(this.service.message(error));
    }
  }
  async refresh(): Promise<void> {
    this.error.set('');
    await this.loadPolicy();
    this.refreshRequested.emit();
  }
  async act(action: 'purchase' | 'cancel' | 'retry'): Promise<void> {
    if (this.busy()) return;
    const current = this.submission();
    this.busy.set(true);
    this.error.set('');
    try {
      if (action === 'retry')
        await this.service.mutations.retry(this.service.priorityAction(current.id));
      else if (action === 'purchase') await this.service.purchase(current.id);
      else if (current.priority?.current_request)
        await this.service.cancelPriority(current.id, current.priority.current_request.id);
      if (this.destroyRef.destroyed) return;
      this.confirming.set(false);
      await this.service.api.refreshWallet();
      this.refreshRequested.emit();
    } catch (error) {
      if (!this.destroyRef.destroyed) {
        this.error.set(this.service.message(error));
        this.refreshRequested.emit();
      }
    } finally {
      if (!this.destroyRef.destroyed) {
        this.pending.set(this.service.mutations.pending(this.service.priorityAction(current.id)));
        this.busy.set(false);
      }
    }
  }
}
