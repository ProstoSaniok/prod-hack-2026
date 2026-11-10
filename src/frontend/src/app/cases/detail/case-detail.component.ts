import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';

import { MockWorkspaceService } from '../data/mock-workspace.service';

@Component({
  selector: 'app-case-detail',
  standalone: true,
  imports: [RouterLink, TuiButton],
  templateUrl: './case-detail.component.html',
  styleUrl: './case-detail.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CaseDetailComponent {
  readonly store = inject(MockWorkspaceService);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly study = computed(() =>
    this.store.cases.find(item => item.id === this.params().get('id')),
  );
  readonly work = computed(() => this.store.workFor(this.study()?.id ?? ''));
  readonly actionLabel = computed(() => {
    const work = this.work();
    return work?.status === 'completed'
      ? 'Смотреть решение'
      : work?.status === 'submitted'
        ? 'Посмотреть отправку'
        : work
          ? 'Продолжить кейс'
          : 'Начать кейс';
  });
}
