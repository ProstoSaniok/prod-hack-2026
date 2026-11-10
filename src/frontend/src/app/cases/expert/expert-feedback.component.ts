import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BackendService } from '../../api/backend.service';
import { ExpertDemoService } from './expert-demo.service';

@Component({
  selector: 'app-expert-feedback',
  standalone: true,
  imports: [RouterLink],
  template: `
    @if (items().length) {
      <section class="surface" aria-label="Обратная связь эксперта">
        <h2>Обратная связь эксперта</h2>
        <p>Оценка качества вашего ревью · Локальное демо</p>
        @for (item of items(); track item.work.id) {
          @if (item.result.quality; as quality) {
            <article class="thread">
              <h3>{{ item.work.example.title }}</h3>
              <p class="badge">
                {{ quality.score }} / 100 ·
                {{ quality.verdict === 'confirmed' ? 'Подтверждено' : 'На доработку' }}
              </p>
              <h3>Сильные стороны</h3>
              <p class="prose">{{ quality.strengths }}</p>
              <h3>Проблемы</h3>
              <p class="prose">{{ quality.issues }}</p>
              <h3>Рекомендации</h3>
              <p class="prose">{{ quality.recommendations }}</p>
              <a [routerLink]="['/demo-results', item.work.id]">Посмотреть итог решения</a>
            </article>
          }
        }
      </section>
    }
  `,
  styleUrl: './expert-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExpertFeedbackComponent {
  private readonly demo = inject(ExpertDemoService);
  private readonly api = inject(BackendService);
  readonly exampleId = input<string | null>(null);
  readonly items = computed(() =>
    this.demo.visibleWorks().flatMap(work => {
      const result = this.demo.results()[work.id];
      return result?.quality &&
        work.review?.reviewerEmail === this.api.user()?.email &&
        (!this.exampleId() || work.id === `reviewed-${this.exampleId()}`)
        ? [{ work, result }]
        : [];
    }),
  );
}
