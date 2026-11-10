import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { CaseStudy, CaseWork } from '../data/case.models';

@Component({
  selector: 'app-case-card',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './case-card.component.html',
  styleUrl: './case-card.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CaseCardComponent {
  readonly study = input.required<CaseStudy>();
  readonly work = input<CaseWork>();
  readonly destination = computed(() =>
    this.work() ? ['/cases', this.study().id, 'work'] : ['/cases', this.study().id],
  );
  readonly action = computed(() => {
    const work = this.work();
    return !work
      ? 'Открыть кейс'
      : work.status === 'completed'
        ? 'Смотреть решение'
        : work.status === 'submitted'
          ? 'На проверке'
          : 'Продолжить';
  });
}
