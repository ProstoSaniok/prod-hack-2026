import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { Review } from './review.service';
import { ProductIconComponent } from '../shared/product-icon.component';
@Component({
  selector: 'app-published-review',
  standalone: true,
  imports: [ProductIconComponent],
  template: ` <section
    class="review-result"
    [class.embedded]="embedded()"
    [attr.aria-label]="
      historical()
        ? 'Редакция ревью'
        : review().status === 'published'
          ? 'Опубликованное ревью'
          : 'Ревью на проверке'
    "
  >
    <h2>
      <app-product-icon name="comment" />{{ historical() ? 'Редакция ревью' : 'Результат ревью' }}
    </h2>
    @if (!historical() && review().status !== 'published') {
      <p>
        {{
          review().status === 'needs_revision'
            ? 'Нужна доработка. Автор решения пока не видит ревью.'
            : 'Ожидает эксперта. Автор решения пока не видит ревью.'
        }}
      </p>
    }
    <p class="verdict">
      Оценка: {{ review().current_revision.score }} / 100 ·
      {{ review().current_revision.verdict === 'approved' ? 'Принято' : 'Нужны изменения' }}
    </p>
    <h3>Сильные стороны</h3>
    <p>{{ review().current_revision.feedback.strengths }}</p>
    <h3>Проблемы</h3>
    <p>{{ review().current_revision.feedback.problems }}</p>
    <h3>Следующие шаги</h3>
    <p>{{ review().current_revision.feedback.next_steps }}</p>
    @if (review().current_revision.comments.length) {
      <h3>Комментарии</h3>
      @for (comment of review().current_revision.comments; track $index) {
        <p>
          @if (comment.path) {
            <strong
              >{{ comment.path }} · строка {{ comment.line
              }}{{
                comment.end_line && comment.end_line !== comment.line ? '–' + comment.end_line : ''
              }}
              · {{ comment.side === 'old' ? 'Исходник' : 'Решение' }}</strong
            ><br />
          }
          {{ comment.body }}
        </p>
      }
    }
  </section>`,
  styles: [
    `
      :host {
        display: block;
        min-width: 0;
      }
      .review-result {
        padding: clamp(16px, 2.5vw, 28px);
        border-radius: var(--prodguild-radius-lg);
        background: var(--prodguild-surface-raised);
      }
      .review-result.embedded {
        padding: 0;
        background: transparent;
      }
      h2 {
        display: flex;
        align-items: center;
        gap: 12px;
        font-size: var(--prodguild-font-section);
      }
      .verdict {
        padding: 12px 16px;
        border-radius: var(--prodguild-radius-sm);
        background: var(--prodguild-surface-muted);
        font-weight: 500;
      }
      h2,
      h3,
      p {
        margin: 16px 0;
      }
      p {
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PublishedReviewComponent {
  readonly review = input.required<Review>();
  readonly embedded = input(false);
  readonly historical = input(false);
}
