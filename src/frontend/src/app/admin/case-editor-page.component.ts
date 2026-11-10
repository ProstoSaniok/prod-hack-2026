import { CdkTrapFocus } from '@angular/cdk/a11y';
import {
  ChangeDetectionStrategy,
  Component,
  afterNextRender,
  Injector,
  computed,
  DestroyRef,
  ElementRef,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { TuiAutoFocus } from '@taiga-ui/cdk';
import { TuiButton, TuiDialog, TuiDialogOptions } from '@taiga-ui/core';
import { defer, of } from 'rxjs';
import { FileWorkspaceComponent } from '../api/file-workspace.component';
import { LearningService, Reference } from '../api/learning.service';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { CompetencyNamesNoticeComponent } from '../shared/competency-names-notice.component';
import { CaseFormComponent } from './case-form/case-form.component';
import {
  CaseFormDraft,
  CaseMetadataPatch,
  CaseRecord,
  validRequiredValue,
} from './cases/case.model';
import { CasesService } from './cases/cases.service';
import { CaseEditorState } from './case-editor.guard';
import { uncertainMutation } from '../api/mutation-recovery';
import { RepositoryWriteContext } from '../api/project-autosave.service';
import { HttpErrorResponse } from '@angular/common/http';
import { LeavePageService } from '../shared/leave-page.service';
import { autoRefresh } from '../shared/auto-refresh';
import { ProductIconComponent } from '../shared/product-icon.component';
import { caseLevelLabel, formatSkillValue } from '../api/case-presentation';

@Component({
  selector: 'app-case-editor-page',
  standalone: true,
  imports: [
    RouterLink,
    TuiButton,
    TuiDialog,
    TuiAutoFocus,
    CdkTrapFocus,
    CaseFormComponent,
    FileWorkspaceComponent,
    LoadingStateComponent,
    CompetencyNamesNoticeComponent,
    ProductIconComponent,
  ],
  templateUrl: './case-editor-page.component.html',
  styleUrl: './case-editor-page.component.css',
  host: { '(window:beforeunload)': 'beforeUnload($event)' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CaseEditorPageComponent implements CaseEditorState {
  readonly caseDifficultyLabel = caseLevelLabel;
  skillLevelLabel(value: number | null): string {
    return validRequiredValue(value)
      ? `Требуемый уровень: ${String(value).replace('.', ',')} из 100`
      : 'Требуемый уровень не указан';
  }
  skillWeightLabel(value: number): string {
    return `${formatSkillValue(value * 100)}%`;
  }
  readonly cases = inject(CasesService);
  private readonly learning = inject(LearningService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly leave = inject(LeavePageService);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  private readonly query = toSignal(this.route.queryParamMap, {
    initialValue: this.route.snapshot.queryParamMap,
  });
  readonly section = computed(() => {
    const value = this.query().get('section');
    return value === 'materials' || value === 'reference' ? value : 'details';
  });
  readonly record = signal<CaseRecord | null>(null);
  readonly formInitial = signal<CaseFormDraft | null>(null);
  readonly reference = signal<Reference | null>(null);
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly refreshing = signal(false);
  readonly error = signal('');
  readonly loadError = signal('');
  readonly syncError = signal('');
  readonly notice = signal('');
  readonly formDirty = signal(false);
  readonly metadataConflict = signal(false);
  private metadataBase: CaseRecord | null = null;
  private readonly caseForm = viewChild(CaseFormComponent);
  readonly materialsDirty = signal(false);
  readonly referenceDirty = signal(false);
  readonly materialsUnsettled = signal(true);
  readonly referenceUnsettled = signal(false);
  readonly materialsWorking = signal(false);
  readonly referenceWorking = signal(false);
  readonly confirming = signal<'publish' | 'delete' | null>(null);
  readonly publishPreparing = signal(false);
  readonly publishError = signal('');
  readonly publishDialogOptions: Partial<TuiDialogOptions<void>> = {
    label: 'Опубликовать кейс?',
    size: 'm',
    closeable: false,
    // Evaluate each dismissal once: an Escape pressed while saving must not
    // become a delayed close when the operation finishes.
    dismissible: defer(() => of(this.canClosePublish())),
  };
  private readonly publishCancel = viewChild<ElementRef<HTMLButtonElement>>('publishCancel');
  private readonly injector = inject(Injector);
  private readonly pageHeading = viewChild<ElementRef<HTMLHeadingElement>>('pageHeading');
  readonly retrying = signal(false);
  readonly prefix = computed(
    () => `/api/v1/admin/cases/${encodeURIComponent(this.record()?.id ?? '')}`,
  );
  readonly caseQuery = computed(() => `expected_case_version=${this.record()?.version ?? 0}`);
  readonly referenceQuery = computed(
    () => `${this.caseQuery()}&expected_reference_version=${this.reference()?.version ?? 0}`,
  );
  readonly locked = computed(() => this.record()?.frozen || this.record()?.status === 'published');
  readonly working = computed(
    () => this.busy() || this.refreshing() || this.materialsWorking() || this.referenceWorking(),
  );
  readonly blocked = computed(() => this.working() || this.retrying() || !!this.syncError());
  readonly unsettled = computed(
    () => this.materialsUnsettled() || this.referenceUnsettled() || this.formDirty(),
  );
  readonly metadataSaving = signal(false);
  readonly draft = signal<CaseFormDraft | null>(null);
  readonly storageError = signal('');
  readonly invalidNumbers = computed(
    () =>
      this.draft()?.competencies.some(
        skill =>
          !validRequiredValue(skill.required_value) ||
          skill.weight === null ||
          skill.weight <= 0 ||
          skill.weight > 0.25,
      ) ?? false,
  );
  readonly saveStatus = computed(() =>
    this.error() || this.syncError()
      ? 'Не удалось сохранить'
      : this.invalidNumbers() && !this.metadataSaving()
        ? 'Проверьте настройки навыков'
        : this.metadataSaving() || this.formDirty()
          ? 'Сохраняем…'
          : this.record()
            ? 'Сохранено'
            : 'Черновик сохранится после первого изменения',
  );
  readonly missingFields = computed(() => {
    const value = this.draft() ?? this.record();
    const missing: string[] = [];
    if (!value?.name.trim()) missing.push('название');
    if (!value?.description.trim()) missing.push('описание');
    if (!value?.expectedResult.trim()) missing.push('ожидаемый результат');
    if (!value?.competencies.length) missing.push('компетенции');
    if (
      value?.competencies.some(
        skill =>
          !validRequiredValue(skill.required_value) ||
          skill.weight === null ||
          skill.weight <= 0 ||
          skill.weight > 0.25,
      )
    )
      missing.push('уровень и вес навыков');
    return missing;
  });
  readonly publicationBlocker = computed(() => {
    if (this.locked()) return 'Содержание этого кейса доступно только для чтения.';
    if (this.missingFields().length)
      return `Для публикации заполните: ${this.missingFields().join(', ')}.`;
    if (this.metadataConflict()) return 'Разрешите конфликт описания перед публикацией.';
    if (this.syncError()) return 'Обновите состояние кейса перед публикацией.';
    if (this.retrying()) return 'Сначала повторите незавершённую операцию с теми же данными.';
    if (this.error()) return 'Сначала устраните ошибку сохранения ниже.';
    if (this.working() || this.publishPreparing()) return 'Дождитесь завершения сохранения.';
    if (!this.reference()?.ready) return 'Добавьте материалы и подтвердите готовность эталона.';
    return '';
  });
  readonly publicationSection = computed(() =>
    this.missingFields().length || this.metadataConflict() || this.formDirty()
      ? 'details'
      : 'reference',
  );
  private readonly materialsWorkspace = viewChild<FileWorkspaceComponent>('materialsWorkspace');
  private readonly referenceWorkspace = viewChild<FileWorkspaceComponent>('referenceWorkspace');
  private draftId = '';
  private createKey = '';
  private restoredBase: CaseFormDraft | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private metadataTask: Promise<boolean> | null = null;
  private metadataFailed = false;
  private queue: Promise<unknown> = Promise.resolve();
  private metadataIntent: { version: number; draft: CaseMetadataPatch; key: string } | null = null;
  readonly materialWrites: RepositoryWriteContext = {
    enqueue: action => this.enqueue(action),
    versions: () => this.caseQuery(),
    completed: () => this.synchronize(),
  };
  readonly referenceWrites: RepositoryWriteContext = {
    enqueue: action => this.enqueue(action),
    versions: () => this.referenceQuery(),
    completed: () => this.synchronize(),
  };
  private routeIdentity: string | null | undefined;
  private generation = 0;
  private leaving = false;
  private pending: (() => Promise<void>) | null = null;

  constructor() {
    autoRefresh(() => this.refresh(), {
      blocked: () =>
        this.loading() ||
        this.hasUnsavedChanges() ||
        this.metadataSaving() ||
        this.working() ||
        this.confirming() === 'publish',
    });
    this.destroyRef.onDestroy(
      inject(LeavePageService).register(
        () =>
          !this.hasUnsavedChanges() ||
          window.confirm(
            'Есть несохранённые изменения или незавершённая операция. Покинуть страницу?',
          ),
      ),
    );
    this.destroyRef.onDestroy(() => {
      this.generation++;
      clearTimeout(this.timer);
    });
    effect(() => {
      const id = this.params().get('id');
      if (id === this.routeIdentity) return;
      this.routeIdentity = id;
      untracked(() => void this.load());
    });
  }

  hasUnsavedChanges(): boolean {
    return (
      !this.leaving &&
      (this.formDirty() ||
        this.materialsDirty() ||
        this.referenceDirty() ||
        this.materialsWorking() ||
        this.referenceWorking() ||
        this.publishPreparing() ||
        this.busy() ||
        this.retrying())
    );
  }
  beforeUnload(event: BeforeUnloadEvent): void {
    if (this.hasUnsavedChanges()) {
      event.preventDefault();
      event.returnValue = '';
    }
  }
  statusLabel(record: CaseRecord): string {
    return record.status === 'draft'
      ? 'Черновик'
      : record.status === 'archived'
        ? 'Архив'
        : 'Опубликован';
  }
  competencyName(id: string): string {
    return this.cases.competencies().find(item => item.id === id)?.name ?? 'Компетенция';
  }
  async load(): Promise<void> {
    const routeId = this.params().get('id');
    if (routeId && routeId === this.record()?.id) return;
    const generation = ++this.generation;
    clearTimeout(this.timer);
    this.metadataTask = null;
    this.metadataFailed = false;
    this.metadataSaving.set(false);
    this.loading.set(true);
    this.loadError.set('');
    this.busy.set(false);
    this.refreshing.set(false);
    this.retrying.set(false);
    this.error.set('');
    this.syncError.set('');
    this.notice.set('');
    this.confirming.set(null);
    this.publishPreparing.set(false);
    this.publishError.set('');
    this.materialsWorking.set(false);
    this.referenceWorking.set(false);
    this.pending = null;
    this.leaving = false;
    this.record.set(null);
    this.formInitial.set(null);
    this.reference.set(null);
    this.formDirty.set(false);
    this.metadataConflict.set(false);
    this.metadataBase = null;
    this.materialsDirty.set(false);
    this.referenceDirty.set(false);
    this.materialsUnsettled.set(true);
    this.referenceUnsettled.set(false);
    try {
      const id = this.params().get('id');
      this.draftId = id ?? this.query().get('draft') ?? '';
      this.restoreDraft();
      void this.cases.loadCompetencies();
      if (id) {
        const record = await this.cases.get(id);
        const reference = await this.learning.reference(id);
        if (generation !== this.generation) return;
        this.record.set(record);
        this.formInitial.set(this.draft() ?? record);
        if (
          this.draft() &&
          this.restoredBase &&
          this.signature(this.restoredBase) !== this.signature(record) &&
          !this.metadataIntent
        )
          this.metadataConflict.set(true);
        this.metadataBase = record;
        this.formDirty.set(
          !!this.draft() && this.signature(this.draft()) !== this.signature(record),
        );
        this.reference.set(reference);
      } else if (this.draft()) {
        this.formInitial.set(this.draft());
        this.formDirty.set(true);
      }
    } catch (error) {
      if (generation === this.generation) this.loadError.set(this.cases.message(error));
    } finally {
      if (generation === this.generation) {
        this.loading.set(false);
        if (this.formDirty()) this.schedule();
      }
    }
  }
  onDraft(draft: CaseFormDraft): void {
    this.draft.set(draft);
    this.formDirty.set(this.signature(draft) !== this.signature(this.metadataBase));
    if (!this.draftId) {
      this.draftId = crypto.randomUUID();
      this.createKey = crypto.randomUUID();
      void this.router.navigate([], {
        relativeTo: this.route,
        queryParams: { draft: this.draftId },
        queryParamsHandling: 'merge',
        replaceUrl: true,
      });
    }
    this.persistDraft();
    if (!this.record()) void this.flushMetadata();
    else this.schedule();
  }
  private schedule(): void {
    clearTimeout(this.timer);
    if (!this.error() && !this.metadataConflict() && !this.locked())
      this.timer = setTimeout(() => void this.flushMetadata(), 800);
  }
  private signature(value: CaseFormDraft | null): string {
    return JSON.stringify(
      value
        ? [
            value.name,
            value.description,
            value.expectedResult,
            [...value.competencies].sort((a, b) => a.competency_id.localeCompare(b.competency_id)),
          ]
        : null,
    );
  }
  private matchesPatch(patch: CaseMetadataPatch, record: CaseRecord): boolean {
    return (
      patch.name === record.name &&
      patch.description === record.description &&
      patch.expectedResult === record.expectedResult &&
      (patch.competencies === undefined ||
        this.signature({ ...patch, competencies: patch.competencies }) === this.signature(record))
    );
  }
  private savableDraft(): CaseMetadataPatch | null {
    const value = this.draft();
    if (!value) return null;
    const text = {
      name: value.name,
      description: value.description,
      expectedResult: value.expectedResult,
    };
    const competencies: NonNullable<CaseMetadataPatch['competencies']>[number][] = [];
    for (const skill of value.competencies) {
      // Keep valid text autosave, but omit the entire skill update until every
      // required level is valid. Never substitute a default for invalid input.
      if (!validRequiredValue(skill.required_value)) return text;
      const previous = this.record()?.competencies.find(
        item => item.competency_id === skill.competency_id,
      );
      competencies.push({
        competency_id: skill.competency_id,
        required_value: skill.required_value,
        weight:
          skill.weight !== null && skill.weight > 0 && skill.weight <= 0.25
            ? skill.weight
            : (previous?.weight ?? 0.1),
      });
    }
    return { ...text, competencies };
  }
  private enqueue<T>(action: () => Promise<T>, metadata = false): Promise<T> {
    return this.serialize(() => {
      if (this.destroyRef.destroyed || this.locked() || this.metadataConflict() || this.syncError())
        throw new Error('Сохранение приостановлено. Проверьте состояние кейса.');
      if (!metadata && this.metadataIntent && this.error())
        throw new Error('Сначала повторите сохранение описания кейса.');
      return action();
    });
  }
  private serialize<T>(action: () => Promise<T>): Promise<T> {
    const task = this.queue.then(action);
    this.queue = task.catch(() => undefined);
    return task;
  }
  private async synchronize(): Promise<void> {
    await this.refresh();
    if (this.syncError()) throw new Error(this.syncError());
  }
  async flushMetadata(retry = false): Promise<boolean> {
    clearTimeout(this.timer);
    if (this.metadataTask) return this.metadataTask;
    if (this.locked() || this.metadataConflict() || this.syncError() || (this.error() && !retry))
      return false;
    if (!this.draftId || !this.draft()) return true;
    const generation = this.generation;
    this.metadataFailed = false;
    this.error.set('');
    const task = this.enqueue(async () => {
      if (generation !== this.generation) return false;
      this.metadataSaving.set(true);
      try {
        if (!this.record()) {
          this.createKey ||= crypto.randomUUID();
          this.persistDraft();
          const created = await this.cases.createDraft(this.draftId, this.createKey);
          if (this.destroyRef.destroyed || generation !== this.generation) return false;
          this.record.set(created);
          this.metadataBase = created;
          this.leaving = true;
          this.leave.approveNextNavigation();
          try {
            await this.router.navigate(['/admin', created.id, 'edit'], { replaceUrl: true });
          } finally {
            this.leave.clearApproval();
            this.leaving = false;
          }
        }
        while (!this.destroyRef.destroyed && generation === this.generation) {
          const current = this.record();
          const draft = this.savableDraft();
          if (!current || !draft) return false;
          if (!this.metadataIntent && this.matchesPatch(draft, current)) break;
          this.metadataIntent ??= { version: current.version, draft, key: crypto.randomUUID() };
          this.persistDraft();
          const intent = this.metadataIntent;
          const saved = await this.cases.saveDraft(
            current.id,
            intent.version,
            intent.draft,
            intent.key,
          );
          if (this.destroyRef.destroyed || generation !== this.generation) return false;
          this.record.set(saved);
          this.metadataBase = saved;
          this.metadataIntent = null;
          this.persistDraft();
        }
        this.formDirty.set(this.signature(this.draft()) !== this.signature(this.metadataBase));
        this.notice.set('');
        this.persistDraft();
        return !this.formDirty();
      } catch (error) {
        if (this.destroyRef.destroyed || generation !== this.generation) return false;
        this.metadataFailed = true;
        // A malformed successful response cannot prove that the write failed.
        if (error instanceof HttpErrorResponse && !uncertainMutation(error))
          this.metadataIntent = null;
        this.error.set(this.cases.message(error));
        if (error instanceof HttpErrorResponse && error.status === 409) await this.refresh();
        this.persistDraft();
        return false;
      } finally {
        if (generation === this.generation) this.metadataSaving.set(false);
      }
    }, true);
    this.metadataTask = task;
    try {
      return await task;
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation) {
        this.metadataFailed = true;
        this.error.set(this.cases.message(error));
      }
      return false;
    } finally {
      if (this.metadataTask === task) this.metadataTask = null;
    }
  }
  private storageKey(): string {
    return `prodguild.case-draft.v1:${this.cases.api.adminUserId()}:${this.draftId}`;
  }
  private persistDraft(): void {
    if (!this.draftId) return;
    try {
      if (
        !this.metadataIntent &&
        this.record() &&
        this.signature(this.draft()) === this.signature(this.record())
      ) {
        sessionStorage.removeItem(this.storageKey());
        return;
      }
      sessionStorage.setItem(
        this.storageKey(),
        JSON.stringify({
          draft: this.draft(),
          base: this.metadataBase,
          createKey: this.createKey,
          intent: this.metadataIntent,
        }),
      );
      this.storageError.set('');
    } catch {
      this.storageError.set(
        'Не удалось сохранить черновик в браузере. Не закрывайте страницу до сохранения.',
      );
    }
  }
  private restoreDraft(): void {
    this.draft.set(null);
    this.metadataIntent = null;
    this.restoredBase = null;
    this.createKey = '';
    if (!this.draftId) return;
    try {
      const raw: unknown = JSON.parse(sessionStorage.getItem(this.storageKey()) ?? 'null');
      if (!raw || typeof raw !== 'object' || !('draft' in raw)) return;
      this.restoredBase = 'base' in raw ? this.parseDraft(raw.base) : null;
      const draft = this.parseDraft(raw.draft);
      if (draft) this.draft.set(draft);
      if ('createKey' in raw && typeof raw.createKey === 'string') this.createKey = raw.createKey;
      if ('intent' in raw && raw.intent && typeof raw.intent === 'object') {
        const intent = raw.intent;
        if (
          'version' in intent &&
          typeof intent.version === 'number' &&
          'key' in intent &&
          typeof intent.key === 'string' &&
          'draft' in intent
        ) {
          const saved = this.parseMetadataPatch(intent.draft);
          if (saved)
            this.metadataIntent = { version: intent.version, key: intent.key, draft: saved };
        }
      }
    } catch {
      this.storageError.set(
        'Не удалось восстановить локальные изменения. Серверный черновик сохранён.',
      );
    }
  }
  private parseMetadataPatch(raw: unknown): CaseMetadataPatch | null {
    if (!raw || typeof raw !== 'object') return null;
    const hasSkills = 'competencies' in raw;
    const parsed = this.parseDraft(hasSkills ? raw : { ...raw, competencies: [] });
    if (!parsed) return null;
    const text = {
      name: parsed.name,
      description: parsed.description,
      expectedResult: parsed.expectedResult,
    };
    if (!hasSkills) return text;
    const competencies: NonNullable<CaseMetadataPatch['competencies']>[number][] = [];
    for (const skill of parsed.competencies) {
      if (
        !validRequiredValue(skill.required_value) ||
        skill.weight === null ||
        !Number.isFinite(skill.weight) ||
        skill.weight <= 0 ||
        skill.weight > 0.25
      )
        return null;
      competencies.push({
        competency_id: skill.competency_id,
        required_value: skill.required_value,
        weight: skill.weight,
      });
    }
    return { ...text, competencies };
  }
  private parseDraft(raw: unknown): CaseFormDraft | null {
    if (
      !raw ||
      typeof raw !== 'object' ||
      !('name' in raw) ||
      typeof raw.name !== 'string' ||
      !('description' in raw) ||
      typeof raw.description !== 'string' ||
      !('expectedResult' in raw) ||
      typeof raw.expectedResult !== 'string' ||
      !('competencies' in raw) ||
      !Array.isArray(raw.competencies)
    )
      return null;
    const competencies: CaseFormDraft['competencies'][number][] = [];
    for (const skill of raw.competencies) {
      if (
        !skill ||
        typeof skill !== 'object' ||
        typeof skill.competency_id !== 'string' ||
        (skill.required_value !== null && typeof skill.required_value !== 'number') ||
        (skill.weight !== null && typeof skill.weight !== 'number')
      )
        return null;
      competencies.push({
        competency_id: skill.competency_id,
        required_value: skill.required_value,
        weight: skill.weight,
      });
    }
    return {
      name: raw.name,
      description: raw.description,
      expectedResult: raw.expectedResult,
      competencies,
    };
  }
  private async flushAll(): Promise<boolean> {
    do {
      if (!(await this.flushMetadata())) return false;
      if (this.materialsWorkspace() && !(await this.materialsWorkspace()?.flush())) return false;
      if (this.referenceWorkspace() && !(await this.referenceWorkspace()?.flush())) return false;
      // Edits may arrive while another repository's request is in flight.
    } while (
      this.formDirty() ||
      this.materialsWorkspace()?.hasPendingChanges() ||
      this.referenceWorkspace()?.hasPendingChanges()
    );
    return !this.destroyRef.destroyed;
  }
  async refresh(): Promise<void> {
    const record = this.record();
    if (!record) return;
    const generation = this.generation;
    this.refreshing.set(true);
    this.syncError.set('');
    try {
      const [updated, reference] = await Promise.all([
        this.cases.get(record.id),
        this.learning.reference(record.id),
      ]);
      if (generation !== this.generation) return;
      this.record.set(updated);
      this.reference.set(reference);
      if (!this.metadataFailed && !this.pending) this.error.set('');
      if (this.metadataBase && this.signature(updated) !== this.signature(this.metadataBase)) {
        if (this.formDirty()) this.metadataConflict.set(true);
        else {
          this.formInitial.set(updated);
          this.draft.set(updated);
          this.metadataBase = updated;
        }
      }
    } catch (error) {
      if (generation === this.generation) this.syncError.set(this.cases.message(error));
    } finally {
      if (generation === this.generation) this.refreshing.set(false);
    }
  }
  async initializeReference(): Promise<void> {
    if (this.blocked() || this.locked() || this.reference() || !(await this.flushAll())) return;
    const path = `${this.prefix()}/reference-solution?${this.caseQuery()}`;
    const generation = this.generation;
    await this.run(async () => {
      await this.learning.mutate('PUT', path);
      if (generation !== this.generation) return;
      await this.refresh();
      if (generation === this.generation)
        this.notice.set('Эталон создан из сохранённых материалов');
    });
  }
  async ready(): Promise<void> {
    const reference = this.reference();
    if (!reference || this.blocked() || this.locked()) return;
    const desiredReady = !reference.ready;
    if (!(await this.flushAll())) return;
    const currentReference = this.reference();
    if (!currentReference) return;
    const path = `${this.prefix()}/reference-solution?${this.caseQuery()}`;
    const body = {
      expected_version: currentReference.version,
      expected_commit_sha: currentReference.head_commit_sha,
      ready: desiredReady,
    };
    const generation = this.generation;
    await this.run(async () => {
      await this.learning.mutate('PATCH', path, body);
      if (generation !== this.generation) return;
      await this.refresh();
      if (generation === this.generation)
        this.notice.set(body.ready ? 'Эталон готов к публикации' : 'Готовность эталона снята');
    });
  }
  openPublish(): void {
    if (!this.record() || this.publicationBlocker()) return;
    this.publishError.set('');
    this.confirming.set('publish');
  }
  canClosePublish(): boolean {
    return !this.publishPreparing() && !this.busy();
  }
  closePublish(): void {
    if (this.canClosePublish()) this.confirming.set(null);
  }
  onPublishDialogChange(open: boolean): void {
    if (!open) this.closePublish();
  }
  private restorePublishFocus(): void {
    afterNextRender(
      () => {
        if (this.confirming() === 'publish' && this.canClosePublish())
          this.publishCancel()?.nativeElement.focus({ preventScroll: true });
      },
      { injector: this.injector },
    );
  }
  async retryPublication(): Promise<void> {
    if (this.publishPreparing() || this.busy()) return;
    const generation = this.generation;
    this.publishPreparing.set(true);
    this.publishError.set('');
    try {
      // Preserve the original mutation closure, payload and idempotency key.
      await this.retry();
    } finally {
      if (generation === this.generation) {
        this.publishPreparing.set(false);
        this.restorePublishFocus();
      }
    }
  }
  async publish(): Promise<void> {
    if (!this.record() || this.publishPreparing() || this.publicationBlocker()) return;
    this.publishPreparing.set(true);
    this.publishError.set('');
    const generation = this.generation;
    try {
      if (!(await this.flushAll())) {
        this.publishError.set('Не все изменения сохранены. Проверьте описание и файлы кейса.');
        return;
      }
      if (!this.reference()?.ready) {
        this.publishError.set('После изменения файлов нужно снова подтвердить готовность эталона.');
        return;
      }
      const path = this.prefix();
      const body = { expected_version: this.record()?.version, status: 'published' };
      await this.run(async () => {
        await this.learning.mutate('PATCH', path, body);
        if (generation !== this.generation) return;
        this.confirming.set(null);
        await this.refresh();
        if (generation === this.generation) {
          this.notice.set('Кейс опубликован');
          // The original button disappears after publication.
          this.pageHeading()?.nativeElement.focus({ preventScroll: true });
        }
      });
    } catch (error) {
      if (generation === this.generation) this.publishError.set(this.cases.message(error));
    } finally {
      if (generation === this.generation) {
        this.publishPreparing.set(false);
        this.restorePublishFocus();
      }
    }
  }
  async changeStatus(status: 'draft' | 'archived'): Promise<void> {
    const record = this.record();
    if (!record || this.blocked() || this.unsettled()) return;
    const generation = this.generation;
    await this.run(async () => {
      const saved = await this.cases.changeStatus(record, status);
      if (generation !== this.generation) return;
      this.record.set(saved);
      this.notice.set(
        status === 'archived' ? 'Кейс перемещён в архив' : 'Кейс возвращён в черновики',
      );
    });
  }
  async remove(): Promise<void> {
    const record = this.record();
    if (!record || this.blocked() || this.locked() || this.unsettled()) return;
    const generation = this.generation;
    await this.run(async () => {
      await this.cases.delete(record);
      if (generation !== this.generation) return;
      this.leaving = true;
      await this.router.navigate(['/admin']);
    });
  }
  async retry(): Promise<void> {
    if (this.pending && !this.busy()) await this.run(this.pending);
    else await this.flushMetadata(true);
  }
  resolveMetadata(keepLocal: boolean): void {
    const record = this.record();
    if (!record || this.blocked()) return;
    if (!keepLocal && !window.confirm('Заменить несохранённое описание версией с сервера?')) return;
    if (!keepLocal) this.formInitial.set(record);
    this.metadataBase = record;
    this.metadataConflict.set(false);
    this.error.set('');
    if (!keepLocal) this.draft.set(record);
    this.formDirty.set(this.signature(this.draft()) !== this.signature(record));
    this.metadataIntent = null;
    this.persistDraft();
    this.schedule();
  }
  discardLockedDescription(): void {
    if (!window.confirm('Заменить несохранённое описание версией с сервера?')) return;
    this.formDirty.set(false);
    this.metadataConflict.set(false);
    this.formInitial.set(this.record());
    this.draft.set(this.record());
    this.metadataBase = this.record();
    this.metadataIntent = null;
    this.persistDraft();
  }
  downloadDescription(): void {
    const draft = this.caseForm()?.form.getRawValue();
    if (!draft) return;
    const contents = `${draft.name}\n\nОписание\n${draft.description}\n\nОжидаемый результат\n${draft.expectedResult}\n\nКомпетенции\n${draft.competencies.map(skill => `${this.competencyName(skill.competency_id)}: ${this.skillLevelLabel(skill.required_value)}, вес ${skill.weight === null ? 'не указан' : this.skillWeightLabel(skill.weight)}`).join('\n')}`;
    const url = URL.createObjectURL(new Blob([contents], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'case-description.txt';
    link.click();
    URL.revokeObjectURL(url);
  }
  private async run(action: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    const generation = this.generation;
    try {
      await this.serialize(action);
      if (generation !== this.generation) return;
      this.pending = null;
      this.retrying.set(false);
    } catch (error) {
      if (generation !== this.generation) return;
      this.error.set(this.cases.message(error));
      this.pending = uncertainMutation(error) ? action : null;
      this.retrying.set(!!this.pending);
    } finally {
      if (generation === this.generation) this.busy.set(false);
    }
  }
}
