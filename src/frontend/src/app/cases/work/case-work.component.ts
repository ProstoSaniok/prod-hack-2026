import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  untracked,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { MockWorkspaceService } from '../data/mock-workspace.service';
import { SolutionWorkspaceComponent } from '../solutions/solution-workspace.component';

@Component({
  selector: 'app-case-work',
  standalone: true,
  imports: [RouterLink, TuiButton, SolutionWorkspaceComponent],
  templateUrl: './case-work.component.html',
  styleUrl: './case-work.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CaseWorkComponent {
  readonly store = inject(MockWorkspaceService);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly study = computed(() =>
    this.store.cases.find(item => item.id === this.params().get('id')),
  );
  readonly work = computed(() => this.store.workFor(this.study()?.id ?? ''));

  constructor() {
    effect(() => {
      const study = this.study();
      untracked(() => {
        if (study) this.store.start(study.id);
      });
    });
  }

  toggleStep(event: { readonly index: number; readonly checked: boolean }): void {
    const study = this.study();
    const work = this.work();
    if (!study || !work) return;
    this.store.save(
      study.id,
      work.draft,
      event.checked
        ? [...work.steps, event.index]
        : work.steps.filter(step => step !== event.index),
    );
  }
}
