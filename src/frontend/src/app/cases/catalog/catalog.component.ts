import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton, TuiDataList, TuiDropdown, TuiTextfield } from '@taiga-ui/core';
import { startWith } from 'rxjs';

import { CaseCardComponent } from '../case-card/case-card.component';
import { CaseCategory, CaseLevel } from '../data/case.models';
import { MockWorkspaceService } from '../data/mock-workspace.service';

type Section = 'catalog' | 'started';

@Component({
  selector: 'app-catalog',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    TuiButton,
    TuiDataList,
    TuiDropdown,
    TuiTextfield,
    CaseCardComponent,
  ],
  templateUrl: './catalog.component.html',
  styleUrl: './catalog.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CatalogComponent {
  readonly workspace = inject(MockWorkspaceService);
  private readonly route = inject(ActivatedRoute);
  private readonly routeData = toSignal(this.route.data, {
    initialValue: this.route.snapshot.data,
  });
  readonly section = computed<Section>(() => {
    const section = this.routeData()['section'];
    return section === 'started' ? section : 'catalog';
  });
  readonly heading = computed(() => {
    const section = this.section();
    return section === 'catalog' ? 'Каталог кейсов' : 'В работе';
  });
  readonly subtitle = computed(() => {
    const section = this.section();
    return section === 'catalog'
      ? 'Выберите кейс по направлению и уровню сложности.'
      : 'Продолжите работу над начатыми кейсами.';
  });
  readonly categories: readonly ('Все' | CaseCategory)[] = [
    'Все',
    'Аналитика',
    'Дизайн',
    'Разработка',
  ];
  readonly levels: readonly CaseLevel[] = ['Начальный', 'Средний', 'Продвинутый'];
  readonly levelOptions = ['', ...this.levels];
  readonly levelOpen = signal(false);
  readonly category = signal<'Все' | CaseCategory>('Все');
  readonly searchControl = new FormControl('', { nonNullable: true });
  readonly levelControl = new FormControl('', { nonNullable: true });
  private readonly query = toSignal(this.searchControl.valueChanges.pipe(startWith('')), {
    initialValue: '',
  });
  private readonly level = toSignal(this.levelControl.valueChanges.pipe(startWith('')), {
    initialValue: '',
  });
  readonly sectionCases = computed(() => {
    const section = this.section();
    return this.workspace.cases.filter(study =>
      section === 'catalog'
        ? true
        : !!this.workspace.workFor(study.id) &&
          this.workspace.workFor(study.id)?.status !== 'completed',
    );
  });
  readonly filteredCases = computed(() => {
    const query = this.query().trim().toLocaleLowerCase('ru');
    return this.sectionCases().filter(study => {
      const matchesQuery = [study.title, ...study.skills].some(value =>
        value.toLocaleLowerCase('ru').includes(query),
      );
      return (
        matchesQuery &&
        (this.category() === 'Все' || study.category === this.category()) &&
        (!this.level() || study.level === this.level())
      );
    });
  });
  readonly countLabel = computed(() => {
    const count = this.filteredCases().length;
    const lastTwo = count % 100;
    const last = count % 10;
    const noun =
      lastTwo >= 11 && lastTwo <= 14
        ? 'кейсов'
        : last === 1
          ? 'кейс'
          : last >= 2 && last <= 4
            ? 'кейса'
            : 'кейсов';
    return `${count} ${noun}`;
  });

  resetFilters(): void {
    this.searchControl.setValue('');
    this.levelControl.setValue('');
    this.category.set('Все');
  }
}
