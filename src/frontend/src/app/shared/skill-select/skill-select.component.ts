import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { toSignal } from '@angular/core/rxjs-interop';
import { TuiButton, TuiDropdown, TuiTextfield } from '@taiga-ui/core';
import { TuiCheckbox } from '@taiga-ui/kit';
import { Competency } from '../../api/backend.service';
import { ProductIconComponent } from '../product-icon.component';
import { normalizeSearch } from '../search-query';

/** A shared, searchable skill picker. Consumers own selection semantics and skill settings. */
@Component({
  selector: 'app-skill-select',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    FormsModule,
    TuiButton,
    TuiDropdown,
    TuiTextfield,
    TuiCheckbox,
    ProductIconComponent,
  ],
  template: `
    <button
      tuiButton
      appearance="secondary"
      size="m"
      type="button"
      class="picker-toggle"
      [disabled]="disabled()"
      [tuiDropdown]="picker"
      [tuiDropdownOpen]="open()"
      (tuiDropdownOpenChange)="setOpen($event)"
      tuiDropdownAlign="left"
      tuiDropdownLimitWidth="min"
      [attr.aria-expanded]="open()"
      aria-haspopup="dialog"
    >
      {{ label() }}{{ selectedIds().length ? ': ' + selectedIds().length : '' }}
      <app-product-icon name="chevron-down" />
    </button>
    <ng-template #picker>
      <section class="picker" role="dialog" [attr.aria-label]="label()">
        <tui-textfield [tuiTextfieldCleaner]="false">
          <input
            tuiTextfield
            type="search"
            [placeholder]="hasMore() ? 'Поиск среди загруженных навыков' : 'Найти навык'"
            [attr.aria-label]="hasMore() ? 'Поиск среди загруженных навыков' : 'Найти навык'"
            [formControl]="search"
          />
        </tui-textfield>
        <div class="options" role="group" [attr.aria-label]="label()">
          @for (skill of filtered(); track skill.id) {
            <label class="option" [class.selected]="selectedIds().includes(skill.id)">
              <input
                tuiCheckbox
                type="checkbox"
                [ngModel]="selectedIds().includes(skill.id)"
                [ngModelOptions]="{ standalone: true }"
                [disabled]="disabled()"
                (ngModelChange)="toggle(skill.id)"
              />
              <span>{{ skill.name }}</span>
            </label>
          } @empty {
            @if (!loading() && !loadError()) {
              <p role="status">
                {{
                  hasMore()
                    ? 'На загруженных страницах навыки не найдены.'
                    : competencies().length
                      ? 'Навыки не найдены.'
                      : 'Навыков пока нет.'
                }}
              </p>
            }
          }
        </div>
        @if (loading()) {
          <p role="status">Загружаем навыки…</p>
        }
        @if (loadError()) {
          <p role="alert">{{ loadError() }}</p>
        }
        @if (hasMore()) {
          <p>Поиск среди загруженных навыков. Для остальных загрузите следующую страницу.</p>
        }
        @if (hasMore() || loadError()) {
          <button
            tuiButton
            appearance="secondary"
            size="s"
            type="button"
            [disabled]="disabled() || loading()"
            (click)="loadMoreRequested.emit()"
          >
            {{ loadError() ? 'Повторить загрузку навыков' : 'Загрузить ещё навыки' }}
          </button>
        }
        <button tuiButton appearance="secondary" size="s" type="button" (click)="open.set(false)">
          Готово
        </button>
      </section>
    </ng-template>
    @if (selectedIds().length) {
      <ul class="selected-skills" aria-label="Выбранные навыки">
        @for (id of selectedIds(); track id) {
          <li>
            <button
              tuiButton
              appearance="secondary"
              size="s"
              type="button"
              [disabled]="disabled()"
              [attr.aria-label]="'Убрать навык ' + name(id)"
              (click)="toggle(id)"
            >
              {{ name(id) }} <app-product-icon name="x" />
            </button>
          </li>
        }
      </ul>
    }
  `,
  styleUrl: './skill-select.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SkillSelectComponent {
  readonly competencies = input<readonly Competency[]>([]);
  readonly selectedIds = input<readonly string[]>([]);
  readonly disabled = input(false);
  readonly label = input('Навыки');
  readonly loading = input(false);
  readonly loadError = input('');
  readonly hasMore = input(false);
  readonly opened = output<void>();
  readonly loadMoreRequested = output<void>();
  readonly selectedIdsChange = output<readonly string[]>();
  readonly open = signal(false);
  readonly search = new FormControl('', { nonNullable: true });
  private readonly query = toSignal(this.search.valueChanges, { initialValue: '' });
  private readonly names = new Intl.Collator('ru', { sensitivity: 'base', numeric: true });
  readonly filtered = computed(() =>
    this.competencies()
      .filter(skill => normalizeSearch(skill.name).includes(normalizeSearch(this.query())))
      .sort((a, b) => this.names.compare(a.name, b.name) || a.id.localeCompare(b.id)),
  );

  name(id: string): string {
    return (
      this.competencies().find(skill => skill.id === id)?.name ??
      `Навык ${this.selectedIds().indexOf(id) + 1}`
    );
  }

  setOpen(open: boolean): void {
    this.open.set(open);
    if (open) this.opened.emit();
  }

  toggle(id: string): void {
    if (this.disabled()) return;
    const ids = this.selectedIds();
    this.selectedIdsChange.emit(
      ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id],
    );
  }
}
