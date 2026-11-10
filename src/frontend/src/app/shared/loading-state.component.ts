import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { TuiLoader } from '@taiga-ui/core';

@Component({
  selector: 'app-loading-state',
  standalone: true,
  imports: [TuiLoader],
  template: `
    <div class="prodguild-loading" role="status">
      <span class="loading-caption"><tui-loader size="s" aria-hidden="true" />{{ label() }}</span>
      <progress [attr.aria-label]="label()"></progress>
      @if (skeleton()) {
        <span class="prodguild-skeleton" aria-hidden="true"></span>
        <span class="prodguild-skeleton" aria-hidden="true"></span>
      }
    </div>
  `,
  styles: [
    `
      .loading-caption {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      progress {
        width: 100%;
        height: 4px;
        accent-color: var(--prodguild-accent);
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoadingStateComponent {
  readonly label = input('Загружаем…');
  readonly skeleton = input(false);
}
