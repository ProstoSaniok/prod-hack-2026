import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { TuiButton, TuiTextfield } from '@taiga-ui/core';
import { LearningService, Project, PublishedCase, Submission } from './learning.service';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { CurrencyAmountComponent } from '../shared/currency-amount.component';
import { normalizeSearch, searchQuery } from '../shared/search-query';
import {
  CASE_DIFFICULTIES,
  SUCCESSFUL_CASE_REWARD,
  caseLevelLabel,
  isCaseDifficulty,
  maximumSkillXp,
  projectStatusLabel,
} from './case-presentation';
import { RemoteFilesComponent } from './remote-files.component';
import { CompetencyCatalogService } from './competency-catalog.service';
import { CompetencyNamesNoticeComponent } from '../shared/competency-names-notice.component';
import { SkillSelectComponent } from '../shared/skill-select/skill-select.component';
import { autoRefresh } from '../shared/auto-refresh';

@Component({
  selector: 'app-learning-catalog',
  standalone: true,
  imports: [
    RouterLink,
    ReactiveFormsModule,
    TuiButton,
    TuiTextfield,
    RemoteFilesComponent,
    LoadingStateComponent,
    CurrencyAmountComponent,
    SkillSelectComponent,
    CompetencyNamesNoticeComponent,
  ],
  host: { '[attr.aria-busy]': 'loading() || busy()' },
  template: `
    <div class="page-intro">
      @if (detailId()) {
        <a class="back-link" routerLink="/catalog">Все кейсы</a>
      }
      <header class="page-heading">
        <div>
          <h1>{{ heading() }}</h1>
        </div>
      </header>
    </div>
    @if (reviewAccessNotice()) {
      <p class="prodguild-feedback" role="status">{{ reviewAccessNotice() }}</p>
      @if (reviewAccessUnavailable()) {
        <a tuiButton appearance="secondary" size="s" routerLink="/reviews"
          >Повторить проверку доступа</a
        >
      }
    }
    @if (error()) {
      <p class="prodguild-feedback" role="alert">{{ error() }}</p>
    }
    @if (loading()) {
      <app-loading-state
        [label]="hasContent() ? 'Обновляем кейсы…' : 'Загружаем кейсы…'"
        [skeleton]="!hasContent()"
      />
    }
    @if (!service.api.user()) {
      <a tuiButton appearance="accent" routerLink="/login">Войти для просмотра кейсов</a>
    } @else {
      @if (error()) {
        <button
          tuiButton
          appearance="secondary"
          size="s"
          type="button"
          [disabled]="loading() || busy()"
          (click)="load()"
        >
          Повторить
        </button>
      }
      @if (detail(); as study) {
        <div class="detail-layout">
          <div class="brief">
            <section class="panel">
              <h2>Задание</h2>
              <p>{{ study.description }}</p>
            </section>
            <section class="panel">
              <h2>Ожидаемый результат</h2>
              <p>{{ study.expected_result }}</p>
            </section>
            <section class="panel">
              <h2>Исходные материалы</h2>
              <app-remote-files [prefix]="'/api/v1/cases/' + study.id" [readOnly]="true" />
            </section>
          </div>
          <aside class="summary">
            <section class="panel start-panel">
              <h2>{{ difficulty(study.difficulty) }}</h2>
              <p class="reward">
                <app-currency-amount [value]="reward" [showPlus]="true" /> за первое успешное
                решение
              </p>
              <button
                tuiButton
                appearance="accent"
                size="l"
                type="button"
                [disabled]="busy() || loading()"
                (click)="start(study.id)"
              >
                {{ busy() ? 'Открываем кейс…' : 'Решать кейс' }}
              </button>
              @if (actionError()) {
                <p role="alert">{{ actionError() }}</p>
              }
            </section>
            <section class="panel">
              <h2>Навыки</h2>
              <ul class="skill-rewards">
                @for (skill of study.competencies; track skill.competency_id) {
                  <li>{{ skillName(skill.competency_id) }}: до +{{ skillXp(skill.weight) }} XP</li>
                }
              </ul>
              <app-competency-names-notice [skills]="study.competencies" />
              <p class="reward-note">XP зависит от оценки решения и уровня проверяющего.</p>
            </section>
          </aside>
        </div>
      } @else if (!detailId()) {
        @if (!isProjects()) {
          <tui-textfield class="search-field" [tuiTextfieldCleaner]="false"
            ><input
              tuiTextfield
              type="search"
              aria-label="Поиск по названию кейса"
              placeholder="Поиск по названию кейса"
              [formControl]="search"
          /></tui-textfield>
          <fieldset class="catalog-filters">
            <legend>Уровень кейса</legend>
            @for (level of levels; track level.value) {
              <button
                tuiButton
                size="s"
                type="button"
                [appearance]="levelFilter() === level.value ? 'accent' : 'secondary'"
                [attr.aria-pressed]="levelFilter() === level.value"
                (click)="updateFilters({ difficulty: level.value })"
              >
                {{ level.label }}
              </button>
            }
          </fieldset>
          <app-skill-select
            [competencies]="competencies()"
            [selectedIds]="selectedSkills()"
            [loading]="skillCatalog.loading()"
            [loadError]="skillCatalog.error()"
            [hasMore]="skillCatalog.hasMore()"
            (opened)="skillCatalog.ensureLoaded()"
            (loadMoreRequested)="skillCatalog.loadMore()"
            (selectedIdsChange)="updateFilters({ competency_ids: $event.join(',') })"
          />
          @if (selectedSkills().length > 1) {
            <p class="filter-note">Любой из выбранных</p>
          }
          @if (!loading() && !error()) {
            <p role="status">На странице: {{ cases().length }}</p>
          }
          @if (levelFilter() || query() || selectedSkills().length) {
            <button tuiButton appearance="flat" size="s" type="button" (click)="resetFilters()">
              Сбросить фильтры
            </button>
          }
          <app-competency-names-notice [skills]="visibleSkills()" />
          <section class="case-list" aria-label="Кейсы">
            @for (study of cases(); track study.id) {
              <a class="server-row" [routerLink]="['/cases', study.id]"
                ><div>
                  <h2>{{ study.title }}</h2>
                  <p>{{ study.description }}</p>
                  <ul class="skill-rewards" aria-label="Награды за навыки">
                    @for (skill of study.competencies; track skill.competency_id) {
                      <li>
                        {{ skillName(skill.competency_id) }}: до +{{ skillXp(skill.weight) }} XP
                      </li>
                    }
                  </ul>
                </div>
                <div class="case-meta">
                  <span>{{ difficulty(study.difficulty) }}</span>
                  <span class="reward"
                    ><app-currency-amount [value]="reward" [showPlus]="true"
                  /></span>
                  <span class="reward-note">За первое успешное решение</span>
                </div></a
              >
            } @empty {
              @if (!loading() && !error()) {
                <p role="status">
                  {{
                    query() || levelFilter() || selectedSkills().length
                      ? 'Ничего не найдено. Измените запрос или фильтры.'
                      : 'Опубликованных кейсов пока нет.'
                  }}
                </p>
              }
            }
          </section>
        } @else {
          <section class="case-list" aria-label="Проекты">
            @if (completed()) {
              @for (submission of submissions(); track submission.id) {
                <a class="server-row" [routerLink]="['/submissions', submission.id]"
                  ><div>
                    <h2>{{ titles().get(submission.case_id) ?? 'Решение по кейсу' }}</h2>
                  </div>
                  <span>{{ statusLabel(submission.status) }} →</span></a
                >
              } @empty {
                @if (!loading() && !error()) {
                  <p>Отправленных решений пока нет.</p>
                }
              }
            } @else {
              @for (project of projects(); track project.id) {
                <a
                  class="server-row"
                  [routerLink]="
                    project.submission_id
                      ? ['/submissions', project.submission_id]
                      : ['/projects', project.id]
                  "
                  ><div>
                    <h2>{{ titles().get(project.case_id) ?? 'Проект по кейсу' }}</h2>
                  </div>
                  <span>{{ statusLabel(project.status) }} →</span></a
                >
              } @empty {
                @if (!loading() && !error()) {
                  <p>
                    {{
                      completed() ? 'Завершённых проектов пока нет.' : 'Начните кейс в каталоге.'
                    }}
                  </p>
                }
              }
            }
          </section>
        }
        @if (offset() > 0 || hasNext()) {
          <div class="paging">
            <button
              tuiButton
              appearance="flat"
              size="s"
              type="button"
              [disabled]="loading() || offset() === 0"
              (click)="page(-1)"
            >
              Назад
            </button>
            <span>Страница {{ offset() / 20 + 1 }}</span>
            <button
              tuiButton
              appearance="flat"
              size="s"
              type="button"
              [disabled]="loading() || !hasNext()"
              (click)="page(1)"
            >
              Далее
            </button>
          </div>
        }
      }
    }
  `,
  styleUrls: [
    '../cases/catalog/catalog.component.css',
    '../cases/detail/case-detail.component.css',
  ],
  styles: [
    `
      .page-intro {
        padding-inline-start: var(--prodguild-page-heading-inset, 0);
        min-height: 44px;
      }
      .catalog-filters {
        display: flex;
        flex-wrap: wrap;
        min-inline-size: 0;
        gap: 8px;
        border: 0;
        padding: 0;
        margin: 20px 0 12px;
      }
      .catalog-filters legend {
        margin-bottom: 10px;
        font-weight: 500;
      }
      .server-row {
        display: flex;
        gap: 16px;
        justify-content: space-between;
        align-items: center;
        color: inherit;
        text-decoration: none;
        padding: 24px 0;
        border-bottom: 1px solid var(--line);
      }
      .server-row div {
        min-width: 0;
        overflow-wrap: anywhere;
      }
      .server-row h2 {
        font-size: var(--prodguild-font-subheading);
      }
      .case-meta {
        display: grid;
        gap: 8px;
        flex: 0 0 210px;
      }
      .reward {
        font-weight: 500;
      }
      .filter-note,
      .reward-note {
        font-size: var(--prodguild-font-caption);
      }
      .skill-rewards {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 20px;
        list-style: none;
        padding: 0;
        margin: 12px 0 0;
      }
      .summary .skill-rewards {
        display: grid;
      }
      .search-field {
        margin-top: 16px;
      }
      .paging {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 12px;
        margin-top: 16px;
      }
      @container (width <= 620px) {
        .server-row {
          flex-direction: column;
          align-items: flex-start;
        }
        .case-list > .server-row {
          padding: 20px;
        }
        .case-meta {
          flex-basis: auto;
        }
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LearningCatalogComponent {
  readonly service = inject(LearningService);
  readonly skillCatalog = inject(CompetencyCatalogService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly router = inject(Router);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  private readonly data = toSignal(this.route.data, { initialValue: this.route.snapshot.data });
  readonly detailId = computed(() => this.params().get('id') ?? '');
  readonly isProjects = computed(() => this.data()['section'] === 'started' || this.completed());
  readonly completed = computed(() => this.data()['section'] === 'completed');
  readonly cases = signal<PublishedCase[]>([]);
  readonly visibleSkills = computed(() => this.cases().flatMap(study => study.competencies));
  readonly detail = signal<PublishedCase | null>(null);
  readonly projects = signal<Project[]>([]);
  readonly submissions = signal<Submission[]>([]);
  readonly titles = signal(new Map<string, string>());
  readonly skills = this.skillCatalog.names;
  readonly competencies = this.skillCatalog.items;
  readonly error = signal('');
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly actionError = signal('');
  readonly offset = signal(0);
  readonly pageLength = signal(0);
  readonly hasNext = computed(() =>
    this.isProjects() ? this.pageLength() >= 20 : this.pageLength() > 20,
  );
  private readonly queryParams = toSignal(this.route.queryParamMap, {
    initialValue: this.route.snapshot.queryParamMap,
  });
  readonly reviewAccessUnavailable = computed(
    () => this.queryParams().get('reviewAccess') === 'unavailable',
  );
  readonly reviewAccessNotice = computed(() =>
    this.reviewAccessUnavailable()
      ? 'Не удалось проверить доступ к ревью. Попробуйте ещё раз.'
      : this.queryParams().get('reviewAccess') === 'denied'
        ? 'Ревью доступно участникам с правами ревьюера.'
        : '',
  );
  readonly levelFilter = computed(() => {
    const value = this.queryParams().get('difficulty') ?? '';
    return isCaseDifficulty(value) ? value : '';
  });
  readonly selectedSkills = computed(() =>
    [...new Set((this.queryParams().get('competency_ids') ?? '').split(',').filter(Boolean))].slice(
      0,
      100,
    ),
  );
  readonly query = computed(() => normalizeSearch(this.queryParams().get('q') ?? ''));
  readonly levels = [{ value: '', label: 'Все уровни' }, ...CASE_DIFFICULTIES];
  resetFilters(): void {
    this.search.reset('');
    this.updateFilters({ q: '', difficulty: '', competency_ids: '' });
  }
  updateFilters(values: Record<string, string>): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {
        ...Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value || null])),
        offset: null,
      },
      queryParamsHandling: 'merge',
      replaceUrl: Object.keys(values).length === 1 && 'q' in values,
    });
  }
  readonly search = new FormControl(this.route.snapshot.queryParamMap.get('q') ?? '', {
    nonNullable: true,
  });
  readonly reward = SUCCESSFUL_CASE_REWARD;
  readonly difficulty = caseLevelLabel;
  readonly skillXp = maximumSkillXp;
  readonly statusLabel = projectStatusLabel;
  readonly hasContent = computed(
    () =>
      !!this.detail() ||
      this.cases().length > 0 ||
      this.projects().length > 0 ||
      this.submissions().length > 0,
  );
  readonly heading = computed(
    () =>
      this.detail()?.title ??
      (this.detailId()
        ? 'Кейс'
        : this.completed()
          ? 'Отправленные решения'
          : this.isProjects()
            ? 'В работе'
            : 'Каталог кейсов'),
  );
  private generation = 0;
  private contentKey = '';
  private targetOffset = 0;
  constructor() {
    this.destroyRef.onDestroy(() => this.generation++);
    searchQuery(this.search.valueChanges)
      .pipe(takeUntilDestroyed())
      .subscribe(q => this.updateFilters({ q }));
    autoRefresh(() => this.load(), {
      blocked: () => this.loading() || this.busy(),
      shouldPoll: () =>
        this.isProjects() &&
        (this.projects().some(project => project.status === 'submitted') ||
          this.submissions().some(submission => submission.status === 'submitted')),
    });
    effect(() => {
      this.detailId();
      this.isProjects();
      this.completed();
      const params = this.queryParams();
      const value = Number(params.get('offset') ?? 0);
      const offset =
        !this.detailId() && !this.isProjects() && Number.isSafeInteger(value) && value >= 0
          ? value
          : 0;
      const contentKey = JSON.stringify([
        this.detailId(),
        this.isProjects(),
        this.completed(),
        this.service.api.user()?.id,
        this.query(),
        this.levelFilter(),
        this.selectedSkills(),
      ]);
      untracked(() => {
        this.search.setValue(params.get('q') ?? '', { emitEvent: false });
        this.actionError.set('');
        if (this.contentKey !== contentKey) {
          this.contentKey = contentKey;
          this.detail.set(null);
          this.cases.set([]);
          this.projects.set([]);
          this.submissions.set([]);
          this.pageLength.set(0);
          this.offset.set(offset);
        }
        this.targetOffset = offset;
        void this.load(offset);
      });
    });
  }
  skillName(id: string): string {
    return this.skills().get(id) ?? 'Навык';
  }
  async load(offset = this.isProjects() ? this.offset() : this.targetOffset): Promise<void> {
    const generation = ++this.generation;
    const detailId = this.detailId();
    const projectsView = this.isProjects();
    const completed = this.completed();
    this.loading.set(true);
    this.error.set('');
    try {
      await this.service.api.initialized;
      if (generation !== this.generation || !this.service.api.user()) return;
      if (!projectsView) void this.skillCatalog.ensureLoaded();
      if (detailId) {
        const value = await this.service.case(detailId);
        if (generation !== this.generation) return;
        this.detail.set(value);
      } else if (projectsView) {
        let projects: readonly (Project | Submission)[];
        if (completed) {
          const submissions = await this.service.submissions(offset);
          if (generation !== this.generation) return;
          this.submissions.set(submissions);
          projects = submissions;
        } else {
          const active = await this.service.projects(offset);
          if (generation !== this.generation) return;
          this.projects.set(active);
          projects = active;
        }
        this.pageLength.set(projects.length);
        this.offset.set(offset);
        const titles = new Map<string, string>();
        const results = await Promise.allSettled(
          projects.map(async p => [p.case_id, (await this.service.case(p.case_id)).title] as const),
        );
        for (const result of results)
          if (result.status === 'fulfilled') titles.set(...result.value);
        if (generation === this.generation) this.titles.set(titles);
      } else {
        const cases = await this.service.catalogue(offset, {
          q: this.query(),
          difficulty: this.levelFilter(),
          competencyIds: this.selectedSkills(),
        });
        if (generation === this.generation) {
          this.cases.set(cases.slice(0, 20));
          this.offset.set(offset);
          this.pageLength.set(cases.length);
        }
      }
    } catch (error) {
      if (generation === this.generation) this.error.set(this.service.message(error));
    } finally {
      if (generation === this.generation) this.loading.set(false);
    }
  }
  async start(id: string): Promise<void> {
    if (this.busy() || this.loading()) return;
    const generation = this.generation;
    this.busy.set(true);
    this.actionError.set('');
    try {
      const project = await this.service.start(id);
      if (generation === this.generation) await this.router.navigate(['/projects', project.id]);
    } catch (error) {
      if (generation === this.generation) this.actionError.set(this.service.message(error));
    } finally {
      this.busy.set(false);
    }
  }
  async page(direction: number): Promise<void> {
    if (this.loading()) return;
    if (!this.detailId() && !this.isProjects()) {
      const offset = Math.max(0, this.offset() + direction * 20);
      if (offset === this.targetOffset) {
        await this.load(offset);
        return;
      }
      await this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { offset: offset || null },
        queryParamsHandling: 'merge',
      });
      return;
    }
    await this.load(Math.max(0, this.offset() + direction * 20));
  }
}
