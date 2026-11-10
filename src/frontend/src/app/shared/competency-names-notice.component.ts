import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import { CompetencyCatalogService } from '../api/competency-catalog.service';

/** Allows resolving missing labels without scanning the catalogue automatically. */
@Component({
  selector: 'app-competency-names-notice',
  standalone: true,
  imports: [TuiButton],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (missing()) {
      <div class="notice">
        <p role="status">
          {{
            catalogue.loading()
              ? 'Загружаем названия навыков…'
              : catalogue.loaded() && !catalogue.hasMore() && !catalogue.error()
                ? 'Названия части навыков недоступны.'
                : 'Названия части навыков ещё не загружены.'
          }}
        </p>
        @if (catalogue.error()) {
          <p role="alert">{{ catalogue.error() }}</p>
        }
        @if (!catalogue.loaded() || catalogue.hasMore() || catalogue.error()) {
          <button
            tuiButton
            appearance="secondary"
            size="s"
            type="button"
            [disabled]="catalogue.loading()"
            (click)="catalogue.loadMore()"
          >
            {{ catalogue.error() ? 'Повторить загрузку навыков' : 'Загрузить ещё навыки' }}
          </button>
        }
      </div>
    }
  `,
  styles: `
    .notice {
      display: grid;
      justify-items: start;
      gap: 12px;
      margin-block: 16px;
    }
    p {
      margin: 0;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    button {
      max-width: 100%;
    }
    [role='alert'] {
      color: var(--tui-text-negative);
    }
  `,
})
export class CompetencyNamesNoticeComponent {
  readonly skills = input.required<readonly { competency_id: string }[]>();
  readonly catalogue = inject(CompetencyCatalogService);
  readonly missing = computed(() =>
    this.skills().some(skill => !this.catalogue.names().has(skill.competency_id)),
  );
}
