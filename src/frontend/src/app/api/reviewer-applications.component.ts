import { autoRefresh } from '../shared/auto-refresh';
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
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { DevelopmentService, ReviewerApplication } from './development.service';

@Component({
  selector: 'app-reviewer-applications',
  standalone: true,
  imports: [DatePipe, RouterLink, TuiButton, LoadingStateComponent, ProductIconComponent],
  template: `
    <section
      class="workflow-panel"
      [attr.aria-label]="mode() === 'expert' ? 'Заявки на экспертный уровень' : 'Мои заявки'"
      [attr.aria-busy]="loading()"
    >
      <div class="heading-row">
        @if (mode() === 'expert') {
          <h1>Заявки на экспертный уровень</h1>
        } @else {
          <h2>Мои заявки</h2>
        }
        @if (error()) {
          <button
            tuiButton
            appearance="flat"
            size="s"
            type="button"
            [disabled]="loading()"
            (click)="load()"
          >
            <app-product-icon name="refresh" /> Повторить загрузку заявок
          </button>
        }
      </div>
      @if (mode() === 'expert') {
        <p>Заявки по навыкам, в которых вы можете оценить готовность будущего эксперта.</p>
      }
      @if (loading()) {
        <app-loading-state label="Загружаем заявки…" [skeleton]="!items().length" />
      }
      @if (error()) {
        <p class="error" role="alert">{{ error() }}</p>
      }
      <ul class="workflow-list">
        @for (item of items(); track item.id) {
          <li>
            <a class="workflow-row" [routerLink]="['/reviewer-applications', item.id]">
              <span
                ><strong>{{ item.competency_name }}</strong
                ><time [attr.datetime]="item.created_at">{{
                  item.created_at | date: 'dd.MM.yyyy HH:mm'
                }}</time></span
              >
              <span
                >{{ service.applicationStatus[item.status] }} <app-product-icon name="arrow-right"
              /></span>
            </a>
          </li>
        } @empty {
          @if (!loading() && !error()) {
            <li class="empty-state">
              <app-product-icon name="award" />
              <p>
                {{
                  mode() === 'expert'
                    ? 'Заявок для вашей проверки пока нет.'
                    : 'Заявок пока нет. Подать заявку можно в карточке доступного навыка.'
                }}
              </p>
            </li>
          }
        }
      </ul>
      @if (offset() > 0 || next() !== null) {
        <nav class="actions" aria-label="Страницы заявок">
          <button
            tuiButton
            appearance="flat"
            type="button"
            [disabled]="loading() || offset() === 0"
            (click)="load(offset() - 20)"
          >
            Предыдущие заявки
          </button>
          <span>Страница {{ offset() / 20 + 1 }}</span>
          <button
            tuiButton
            appearance="flat"
            type="button"
            [disabled]="loading() || next() === null"
            (click)="load(next() ?? offset())"
          >
            Следующие заявки
          </button>
        </nav>
      }
    </section>
  `,
  styleUrl: './workflow-panels.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewerApplicationsComponent {
  private readonly route = inject(ActivatedRoute);
  readonly mode = input<'owner' | 'expert'>(
    this.route.snapshot.data['applications'] === 'expert' ? 'expert' : 'owner',
  );
  readonly service = inject(DevelopmentService);
  private readonly destroyRef = inject(DestroyRef);
  readonly items = signal<ReviewerApplication[]>([]);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly offset = signal(0);
  readonly next = signal<number | null>(null);
  private requestedOffset = 0;
  private generation = 0;
  constructor() {
    autoRefresh(() => this.load(), {
      blocked: () => this.loading(),
      shouldPoll: () =>
        this.mode() === 'expert' || this.items().some(item => item.status === 'pending'),
    });
    effect(() => {
      this.mode();
      untracked(() => void this.load(0));
    });
  }
  async load(offset = this.error() ? this.requestedOffset : this.offset()): Promise<void> {
    const generation = ++this.generation;
    const owner = this.service.api.user()?.id;
    this.requestedOffset = offset;
    this.loading.set(true);
    this.error.set('');
    try {
      const page = await this.service.applications(this.mode() === 'expert', offset);
      if (
        this.destroyRef.destroyed ||
        generation !== this.generation ||
        this.service.api.user()?.id !== owner
      )
        return;
      this.items.set(page.items);
      this.offset.set(offset);
      this.next.set(page.next_offset);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.loading.set(false);
    }
  }
}
