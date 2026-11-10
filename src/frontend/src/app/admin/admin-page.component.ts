import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TuiButton, TuiDataList, TuiTextfield } from '@taiga-ui/core';

import { LoadingStateComponent } from '../shared/loading-state.component';
import { normalizeSearch, searchQuery } from '../shared/search-query';
import { autoRefresh } from '../shared/auto-refresh';

import { CaseStatusFilter, CasesService } from './cases/cases.service';

type StatusChoice = 'Все статусы' | 'Черновики' | 'Опубликованные' | 'Архив';

@Component({
  selector: 'app-admin-page',
  standalone: true,
  imports: [
    DatePipe,
    LoadingStateComponent,
    ReactiveFormsModule,
    RouterLink,
    TuiButton,
    TuiDataList,
    TuiTextfield,
  ],
  templateUrl: './admin-page.component.html',
  styleUrl: './admin-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminPageComponent {
  readonly repository = inject(CasesService);
  readonly search = new FormControl('', { nonNullable: true });
  readonly searchValue = toSignal(searchQuery(this.search.valueChanges), { initialValue: '' });
  readonly statuses: readonly { value: CaseStatusFilter; label: StatusChoice }[] = [
    { value: '', label: 'Все статусы' },
    { value: 'draft', label: 'Черновики' },
    { value: 'published', label: 'Опубликованные' },
    { value: 'archived', label: 'Архив' },
  ];
  readonly statusFilter = new FormControl<StatusChoice>(this.choiceFor(this.repository.status()), {
    nonNullable: true,
  });
  readonly filteredCases = computed(() => {
    const query = normalizeSearch(this.searchValue());
    return this.repository.cases().filter(record => normalizeSearch(record.name).includes(query));
  });

  constructor() {
    autoRefresh(() => this.repository.load(this.repository.offset(), this.repository.status()), {
      blocked: () => this.repository.loading(),
    });
    effect(() => {
      if (this.repository.loading()) this.statusFilter.disable({ emitEvent: false });
      else {
        const confirmed = this.choiceFor(this.repository.status());
        if (this.statusFilter.value !== confirmed) {
          this.statusFilter.setValue(confirmed, { emitEvent: false });
        }
        this.statusFilter.enable({ emitEvent: false });
      }
    });
    this.statusFilter.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(value => void this.filterStatus(value));
    void this.repository.load();
  }

  async page(direction: number): Promise<void> {
    if (this.repository.loading()) return;
    if (direction > 0 && !this.repository.hasNext()) return;
    await this.repository.load(
      Math.max(0, this.repository.offset() + direction * this.repository.pageSize),
    );
  }

  private async filterStatus(value: StatusChoice): Promise<void> {
    await this.repository.load(0, this.statuses.find(item => item.label === value)?.value ?? '');
  }

  private choiceFor(value: CaseStatusFilter): StatusChoice {
    return this.statuses.find(item => item.value === value)?.label ?? 'Все статусы';
  }
}
