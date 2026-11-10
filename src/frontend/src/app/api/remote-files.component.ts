import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { TuiButton } from '@taiga-ui/core';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { LearningService, RemoteFile } from './learning.service';
import { RepositoryEditorComponent } from '../cases/solutions/repository-editor/repository-editor.component';
import { uncertainMutation } from './mutation-recovery';
import { ProjectAutosaveService, RepositoryWriteContext } from './project-autosave.service';
import { ProductIconComponent } from '../shared/product-icon.component';
import { WorkspaceMaximizeDirective } from '../shared/workspace-maximize/workspace-maximize.directive';
import { autoRefresh } from '../shared/auto-refresh';

@Component({
  selector: 'app-remote-files',
  standalone: true,
  providers: [ProjectAutosaveService],
  hostDirectives: [WorkspaceMaximizeDirective],
  imports: [TuiButton, RepositoryEditorComponent, LoadingStateComponent, ProductIconComponent],
  host: { '[attr.aria-busy]': 'busy() || uploading() || fileLoading()' },
  template: `
    @if (busy() || fileLoading()) {
      <app-loading-state [label]="operation() || 'Загружаем файлы…'" [skeleton]="!files().length" />
    }
    @if (visibleError()) {
      <p role="alert">{{ visibleError() }}</p>
      @if (autosave() && unsavedCount() > 0 && recovering()) {
        <button tuiButton appearance="secondary" size="s" type="button" (click)="downloadDrafts()">
          Скачать все черновики
        </button>
      }
      @if (retrying() || (autosave() && auto.error())) {
        <button
          tuiButton
          appearance="secondary"
          size="s"
          type="button"
          [disabled]="busy() || uploading() || fileLoading() || auto.saving()"
          (click)="retry()"
        >
          Повторить сохранение
        </button>
      }
      @if (selected() && !readOnly() && !recovering()) {
        <button
          tuiButton
          appearance="secondary"
          size="s"
          type="button"
          [disabled]="busy() || uploading() || fileLoading()"
          (click)="confirmReload.set(true)"
        >
          Загрузить серверную версию файла
        </button>
      }
    }
    @if ((readOnly() || visibleError()) && unsavedCount() > 0 && !recovering()) {
      <p role="status">
        Есть несохранённые файлы. Скачайте свои изменения перед заменой серверной версией.
      </p>
      <button tuiButton appearance="secondary" size="s" type="button" (click)="downloadDrafts()">
        Скачать все черновики
      </button>
      <button
        tuiButton
        appearance="secondary"
        size="s"
        type="button"
        [disabled]="busy() || uploading() || fileLoading()"
        (click)="discardDrafts()"
      >
        Отменить изменения файлов
      </button>
    }
    @if (confirmReload()) {
      <p>
        Несохранённые изменения выбранного файла будут заменены серверной версией. Сначала можно
        скачать свой черновик.
      </p>
      <button tuiButton appearance="secondary" size="s" type="button" (click)="download()">
        Скачать черновик
      </button>
      <button tuiButton appearance="secondary" size="s" type="button" (click)="reloadCurrent()">
        Заменить серверной версией
      </button>
      <button tuiButton appearance="flat" size="s" type="button" (click)="confirmReload.set(false)">
        Отмена
      </button>
    }
    <div class="toolbar maximize-toolbar">
      @if (visibleError() && !recovering()) {
        <button
          tuiButton
          appearance="secondary"
          size="s"
          type="button"
          [disabled]="busy() || uploading() || fileLoading()"
          (click)="reloadFiles()"
        >
          Повторить загрузку
        </button>
      }
      @if (allowMaximize() && maximize.available) {
        <button
          #maximizeButton
          tuiButton
          appearance="flat"
          size="s"
          type="button"
          class="maximize-toggle"
          [attr.aria-expanded]="maximize.expanded()"
          [attr.aria-controls]="maximize.id"
          (click)="maximize.toggle(maximizeButton)"
        >
          <app-product-icon [name]="maximize.expanded() ? 'collapse' : 'expand'" />
          {{ maximize.expanded() ? 'Свернуть код' : 'Развернуть код' }}
        </button>
      }
    </div>
    @if (confirmDelete()) {
      <p>Удалить файл «{{ selected() }}»?</p>
      <button
        tuiButton
        appearance="secondary"
        size="s"
        type="button"
        [disabled]="busy() || uploading() || fileLoading()"
        (click)="remove()"
      >
        {{ operation() === 'Удаляем файл…' ? operation() : 'Подтвердить удаление файла' }}
      </button>
      <button tuiButton appearance="flat" size="s" type="button" (click)="confirmDelete.set(false)">
        Отмена
      </button>
    }
    <div class="files-editor">
      <input #fileUpload type="file" multiple hidden (change)="upload($event)" />
      <app-repository-editor
        [files]="documents()"
        [scopeId]="prefix()"
        [selectedId]="selected()"
        [content]="content()"
        [readOnly]="
          readOnly() || uploading() || structuralRecovery() || (!autosave() && retrying())
        "
        [busy]="busy() || uploading() || fileLoading()"
        [status]="
          selected() && !selectedFileReady()
            ? 'Файл не загружен'
            : autosave()
              ? auto.status()
              : notice()
        "
        [showSave]="!autosave()"
        [showExplorer]="true"
        [responsiveExplorer]="true"
        [allowCreate]="!recovering()"
        [creating]="creating() && !readOnly()"
        [createDisabled]="recovering()"
        [allowUpload]="!recovering()"
        [emptyTitle]="busy() ? 'Загрузка файлов' : error() ? 'Файлы недоступны' : ''"
        [emptyDescription]="
          busy() || error()
            ? ''
            : readOnly()
              ? 'Материалы ещё не добавлены.'
              : 'Добавьте файл, чтобы начать работу.'
        "
        (fileSelect)="select($event)"
        (contentChange)="edit($event)"
        (save)="save()"
        (create)="creating.set(true)"
        (fileCreate)="create($event)"
        (createCancel)="creating.set(false)"
        (upload)="fileUpload.click()"
        (remove)="confirmDelete.set(true)"
        (download)="download()"
      />
    </div>
    @if (autosave() && auto.storageError()) {
      <p role="alert">{{ auto.storageError() }}</p>
    }
  `,
  styles: [
    `
      :host {
        display: block;
        min-width: 0;
      }
      .toolbar {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin: 12px 0;
        align-items: center;
      }
      .files-editor {
        height: 500px;
        min-width: 0;
      }
      .hint {
        font-size: var(--prodguild-font-caption);
        color: var(--muted);
      }
      app-repository-editor {
        height: 100%;
      }
      .maximize-toggle {
        margin-inline-start: auto;
      }
      :host([data-code-maximize-target]) .files-editor {
        flex: 1 1 240px;
        min-height: 240px;
        height: auto;
      }
      :host([data-code-maximize-target]) > :not(.files-editor) {
        flex-shrink: 0;
      }
      :host-context(.workspace[data-code-maximize-target]) {
        display: flex;
        flex-direction: column;
        min-height: 0;
      }
      :host-context(.workspace[data-code-maximize-target]) .files-editor {
        flex: 1 1 240px;
        min-height: 240px;
        height: auto;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RemoteFilesComponent {
  readonly allowMaximize = input(true);
  readonly maximize = inject(WorkspaceMaximizeDirective);
  readonly service = inject(LearningService);
  private readonly destroyRef = inject(DestroyRef);
  readonly prefix = input.required<string>();
  readonly versions = input('');
  readonly autosave = input(false);
  readonly writeContext = input<RepositoryWriteContext>();
  readonly auto = inject(ProjectAutosaveService);
  private readonly owner = computed(() =>
    this.prefix().startsWith('/api/v1/admin/')
      ? this.service.api.adminUserId()
      : this.service.api.user()?.id,
  );
  readonly structuralRecovery = computed(
    () => this.autosave() && !!this.auto.pending() && this.auto.pending()?.kind !== 'save',
  );
  readonly visibleError = computed(
    () => this.error() || (this.autosave() ? this.auto.error() : ''),
  );
  readonly recovering = computed(() => (this.autosave() ? !!this.auto.pending() : this.retrying()));
  readonly unsavedCount = computed(() =>
    this.autosave() ? this.auto.drafts().size : this.draftCount(),
  );
  readonly readOnly = input(false);
  readonly allowEmpty = input(false);
  readonly changed = output<void>();
  readonly refreshRequested = output<void>();
  readonly unsettled = output<boolean>();
  readonly dirty = output<boolean>();
  readonly working = output<boolean>();
  readonly retrying = signal(false);
  private pending: { label: string; action: () => Promise<unknown>; onSuccess: () => void } | null =
    null;
  readonly draftCount = signal(0);
  readonly files = signal<RemoteFile[]>([]);
  readonly selected = signal('');
  readonly content = signal('');
  readonly busy = signal(false);
  readonly uploading = signal(false);
  private uploadBatch: Promise<void> | null = null;
  readonly operation = signal('');
  readonly fileLoading = signal(false);
  readonly selectedFileReady = signal(false);
  readonly confirmReload = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  readonly creating = signal(false);
  readonly confirmDelete = signal(false);
  private drafts = new Map<string, string>();
  private loaded = new Map<string, RemoteFile>();
  private generation = 0;
  private scopeGeneration = 0;
  private selectionGeneration = 0;
  readonly documents = computed(() =>
    this.files().map(file => ({
      id: file.path,
      path: file.path,
      content: '',
      readOnly: file.path === this.selected() && !this.selectedFileReady(),
      modified: this.autosave() && this.auto.drafts().has(file.path),
    })),
  );
  hasPendingChanges(): boolean {
    return (
      this.uploading() ||
      (this.autosave() ? this.auto.unresolved() : this.draftCount() > 0 || this.retrying())
    );
  }
  constructor() {
    autoRefresh(() => this.reloadFiles(), {
      blocked: () =>
        this.busy() ||
        this.uploading() ||
        this.fileLoading() ||
        this.recovering() ||
        this.auto.saving() ||
        this.unsavedCount() > 0 ||
        this.creating() ||
        this.confirmDelete() ||
        this.confirmReload(),
    });
    effect(() =>
      this.dirty.emit(
        this.uploading() ||
          (this.autosave() ? this.auto.unresolved() : this.draftCount() > 0 || this.retrying()),
      ),
    );
    effect(() =>
      this.working.emit(
        this.busy() ||
          this.uploading() ||
          this.fileLoading() ||
          this.retrying() ||
          (this.autosave() && this.auto.saving()),
      ),
    );
    effect(() =>
      this.unsettled.emit(
        this.busy() ||
          this.uploading() ||
          this.fileLoading() ||
          this.unsavedCount() > 0 ||
          !!this.visibleError() ||
          this.retrying() ||
          (this.autosave() && this.auto.unresolved()),
      ),
    );
    effect(() => {
      if (this.autosave()) this.auto.setReadOnly(this.readOnly());
    });
    effect(() => {
      const change = this.auto.lastWrite();
      if (!change || !this.autosave()) return;
      untracked(() => {
        if (change.file) {
          const file = change.file;
          this.loaded.set(file.path, file);
          this.files.update(files =>
            files.some(item => item.path === file.path)
              ? files.map(item => (item.path === file.path ? file : item))
              : [...files, file],
          );
          if (change.kind === 'create') {
            this.selected.set(file.path);
            this.selectedFileReady.set(true);
            this.content.set(this.auto.content(file.path) ?? file.content ?? '');
            this.creating.set(false);
          }
        } else {
          this.loaded.delete(change.path);
          this.files.update(files => files.filter(file => file.path !== change.path));
          this.confirmDelete.set(false);
          if (this.selected() === change.path) void this.select(this.files()[0]?.path ?? '');
        }
        if (!this.writeContext() && change.kind !== 'save') this.changed.emit();
      });
    });
    this.destroyRef.onDestroy(() => {
      this.auto.stop();
      this.scopeGeneration++;
      this.generation++;
      this.selectionGeneration++;
    });
    effect(() => {
      this.prefix();
      this.autosave();
      this.owner();
      untracked(() => {
        this.scopeGeneration++;
        this.selectionGeneration++;
        this.uploadBatch = null;
        this.uploading.set(false);
        this.files.set([]);
        this.selectedFileReady.set(false);
        this.fileLoading.set(false);
        this.operation.set('');
        this.notice.set('');
        this.confirmReload.set(false);
        this.confirmDelete.set(false);
        this.creating.set(false);
        this.pending = null;
        this.retrying.set(false);
        this.drafts.clear();
        this.loaded.clear();
        if (this.autosave()) {
          this.auto.configure(this.prefix(), this.writeContext());
          this.auto.setReadOnly(this.readOnly());
        } else this.restoreDrafts();
        this.selected.set('');
        this.content.set('');
        void this.load();
      });
    });
  }
  private query(path?: string, sha?: string): string {
    return (
      '?' +
      [
        this.versions(),
        path === undefined ? '' : 'path=' + encodeURIComponent(path),
        sha === undefined ? '' : 'sha=' + encodeURIComponent(sha),
      ]
        .filter(Boolean)
        .join('&')
    );
  }
  async load(): Promise<void> {
    if (this.retrying()) return;
    const generation = ++this.generation;
    const prefix = this.prefix();
    this.busy.set(true);
    this.error.set('');
    try {
      const files = await this.service.files(prefix);
      if (generation !== this.generation) return;
      this.files.set(files);
      const path = files.some(file => file.path === this.selected())
        ? this.selected()
        : (files[0]?.path ?? '');
      if (path) await this.select(path);
      else {
        this.selected.set('');
        this.selectedFileReady.set(false);
        this.content.set('');
      }
    } catch (error) {
      if (generation !== this.generation) return;
      if (this.allowEmpty() && error instanceof HttpErrorResponse && error.status === 404)
        this.files.set([]);
      else this.error.set(this.service.message(error));
    } finally {
      if (generation === this.generation) {
        this.busy.set(false);
        if (this.autosave()) this.auto.schedule();
      }
    }
  }
  async reloadFiles(): Promise<void> {
    if (
      this.busy() ||
      this.uploading() ||
      this.fileLoading() ||
      this.recovering() ||
      this.auto.saving()
    )
      return;
    this.refreshRequested.emit();
    await this.load();
  }
  async select(path: string, replaceDraft = false): Promise<void> {
    if (this.operation() && !this.autosave()) return;
    const prefix = this.prefix();
    const readRevision = this.auto.revision(path);
    const generation = this.generation;
    const selectionGeneration = ++this.selectionGeneration;
    this.fileLoading.set(true);
    this.selected.set(path);
    this.selectedFileReady.set(this.loaded.has(path));
    this.content.set(
      (this.autosave() ? this.auto.content(path) : this.drafts.get(path)) ??
        this.loaded.get(path)?.content ??
        '',
    );
    this.confirmDelete.set(false);
    if (!path) {
      this.content.set('');
      this.fileLoading.set(false);
      return;
    }
    try {
      const file = await this.service.file(prefix, path);
      if (generation !== this.generation || selectionGeneration !== this.selectionGeneration)
        return;
      if (file.path !== path || typeof file.content !== 'string' || !file.sha || !file.commit_sha)
        throw new Error('Некорректный ответ сервера. Повторите загрузку файла.');
      this.selectedFileReady.set(true);
      this.error.set('');
      if (this.autosave()) {
        if (replaceDraft && this.auto.replace(file, readRevision)) {
          this.confirmReload.set(false);
          this.error.set('');
        }
        this.content.set(this.auto.accept(file, readRevision));
        this.loaded.set(path, this.auto.baseline(path) ?? file);
        return;
      }
      if (replaceDraft) {
        this.drafts.delete(path);
        this.persistDrafts();
        this.confirmReload.set(false);
        this.error.set('');
        this.notice.set('Загружена серверная версия файла');
      }
      // Keep the SHA that belongs to an unsaved draft so concurrent changes produce a conflict.
      if (!this.drafts.has(path)) this.loaded.set(path, file);
      this.content.set(this.drafts.get(path) ?? file.content ?? '');
    } catch (error) {
      if (generation === this.generation && selectionGeneration === this.selectionGeneration)
        this.error.set(this.service.message(error));
    } finally {
      if (selectionGeneration === this.selectionGeneration) this.fileLoading.set(false);
    }
  }
  edit(content: string): void {
    if (
      !this.selectedFileReady() ||
      this.fileLoading() ||
      this.busy() ||
      this.uploading() ||
      this.readOnly() ||
      this.structuralRecovery() ||
      (!this.autosave() && this.retrying())
    )
      return;
    this.content.set(content);
    if (this.autosave()) {
      const file = this.loaded.get(this.selected());
      if (file) this.auto.edit(file, content);
      return;
    }
    this.drafts.set(this.selected(), content);
    this.persistDrafts();
    this.notice.set('Есть несохранённые изменения');
  }
  async create(path: string): Promise<void> {
    if (
      this.busy() ||
      this.uploading() ||
      this.fileLoading() ||
      this.readOnly() ||
      this.recovering()
    )
      return;
    if (!path) return;
    if (this.autosave()) {
      await this.autoStructure('Создаём файл…', () => this.auto.create(path));
      return;
    }
    const url = this.prefix() + '/files' + this.query();
    await this.write(
      'Создаём файл…',
      () => this.service.mutate('POST', url, { path, content: '' }),
      () => {
        this.selected.set(path);
        this.creating.set(false);
      },
    );
  }
  async upload(event: Event): Promise<void> {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || this.readOnly() || this.busy() || this.uploading())
      return;
    const files = [...(input.files ?? [])];
    input.value = '';
    if (!files.length) return;
    const scope = this.scopeGeneration;
    this.error.set('');
    this.uploading.set(true);
    const task = this.uploadFiles(files, scope);
    this.uploadBatch = task;
    try {
      await task;
    } finally {
      if (this.uploadBatch === task) {
        this.uploadBatch = null;
        this.uploading.set(false);
      }
    }
  }
  private async uploadFiles(files: readonly File[], scope: number): Promise<void> {
    if (!(await this.flushWrites()) || scope !== this.scopeGeneration) return;
    let sourceBytes = this.files().reduce(
      (total, file) => total + (file.size ?? new TextEncoder().encode(file.content ?? '').length),
      0,
    );
    const sourcePaths = new Set(this.files().map(file => file.path));
    for (const file of files) {
      if (scope !== this.scopeGeneration || this.destroyRef.destroyed || this.readOnly()) return;
      let content: string | null = null;
      if (
        file.size <= 1_048_576 &&
        !/\.(pdf|zip|gz|png|jpe?g|gif|webp|docx?|xlsx?|pptx?)$/i.test(file.name)
      ) {
        try {
          const bytes = new Uint8Array(await file.arrayBuffer());
          if (!bytes.includes(0)) content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        } catch {
          content = null;
        }
      }
      if (scope !== this.scopeGeneration || this.destroyRef.destroyed || this.readOnly()) return;
      if (content === null) {
        this.error.set('В исходники можно загрузить UTF-8 текст до 1 МиБ.');
        return;
      }
      const contentBytes = new TextEncoder().encode(content).length;
      if (sourceBytes + contentBytes > 1_048_576 || sourcePaths.size >= 100) {
        this.error.set('В исходниках может быть до 100 файлов общим размером до 1 МиБ.');
        return;
      }
      let path = file.name;
      if (sourcePaths.has(path)) {
        const renamed = window.prompt(`Файл «${path}» уже есть. Укажите другое имя:`, path);
        if (!renamed) return;
        path = renamed.trim();
      }
      if (sourcePaths.has(path)) {
        this.error.set('Такой файл уже есть. Выберите другое имя.');
        return;
      }
      if (this.autosave()) {
        const initial = content;
        await this.autoStructure('Загружаем файл…', () => this.auto.create(path, initial));
        if (this.auto.error()) return;
      } else {
        const initial = content;
        await this.write(
          'Загружаем файл…',
          () =>
            this.service.mutate('POST', this.prefix() + '/files' + this.query(), {
              path,
              content: initial,
            }),
          () => this.selected.set(path),
        );
        if (this.error()) return;
      }
      if (scope !== this.scopeGeneration || this.destroyRef.destroyed) return;
      sourceBytes += contentBytes;
      sourcePaths.add(path);
    }
  }
  async save(): Promise<void> {
    if (this.autosave()) {
      await this.flush();
      return;
    }
    const file = this.loaded.get(this.selected());
    if (!file || this.busy() || this.fileLoading() || this.readOnly() || this.retrying()) return;
    const url = this.prefix() + '/files/content' + this.query(file.path);
    const body = { sha: file.sha, content: this.content() };
    await this.write(
      'Сохраняем файл…',
      () => this.service.mutate('PUT', url, body),
      () => {
        this.drafts.delete(file.path);
        this.loaded.delete(file.path);
        this.selectedFileReady.set(false);
        this.persistDrafts();
      },
    );
  }
  async remove(): Promise<void> {
    const file = this.loaded.get(this.selected());
    if (!file || this.busy() || this.fileLoading() || this.readOnly() || this.retrying()) return;
    if (this.autosave()) {
      await this.autoStructure('Удаляем файл…', () => this.auto.remove(file));
      return;
    }
    const url = this.prefix() + '/files/content' + this.query(file.path, file.sha);
    await this.write(
      'Удаляем файл…',
      () => this.service.mutate('DELETE', url),
      () => {
        this.drafts.delete(file.path);
        this.loaded.delete(file.path);
        this.selectedFileReady.set(false);
        this.persistDrafts();
        this.confirmDelete.set(false);
      },
    );
  }
  async flush(): Promise<boolean> {
    const scope = this.scopeGeneration;
    if (this.uploadBatch) await this.uploadBatch;
    if (scope !== this.scopeGeneration || this.destroyRef.destroyed) return false;
    return this.flushWrites();
  }
  private async flushWrites(): Promise<boolean> {
    if (!this.autosave()) return !this.draftCount() && !this.busy() && !this.retrying();
    if (this.busy() || this.fileLoading() || this.error()) return false;
    return this.auto.flush();
  }
  private async autoStructure(label: string, action: () => Promise<boolean>): Promise<void> {
    const scope = this.scopeGeneration;
    this.busy.set(true);
    this.operation.set(label);
    try {
      await action();
    } finally {
      if (scope === this.scopeGeneration) {
        this.busy.set(false);
        this.operation.set('');
      }
    }
  }
  private async write(
    label: string,
    action: () => Promise<unknown>,
    onSuccess: () => void,
  ): Promise<void> {
    const scope = this.scopeGeneration;
    this.busy.set(true);
    this.operation.set(label);
    this.error.set('');
    try {
      await action();
      if (scope !== this.scopeGeneration) return;
      onSuccess();
      this.pending = null;
      this.retrying.set(false);
      this.notice.set('Сохранено на сервере');
      this.changed.emit();
      this.operation.set('');
      await this.load();
    } catch (error) {
      if (scope === this.scopeGeneration) {
        this.error.set(this.service.message(error));
        this.pending = uncertainMutation(error) ? { label, action, onSuccess } : null;
        this.retrying.set(!!this.pending);
      }
    } finally {
      if (scope === this.scopeGeneration) {
        this.operation.set('');
        this.busy.set(false);
      }
    }
  }
  async retry(): Promise<void> {
    if (this.autosave()) {
      await this.auto.flush(true);
      return;
    }
    if (!this.pending || this.busy() || this.fileLoading()) return;
    const { label, action, onSuccess } = this.pending;
    await this.write(label, action, onSuccess);
  }
  download(): void {
    const url = URL.createObjectURL(
      new Blob([this.content()], { type: 'text/plain;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = this.selected().split('/').at(-1) ?? 'file.txt';
    link.click();
    URL.revokeObjectURL(url);
  }
  private storageKey(): string {
    return `prodguild.remote-drafts.v1:${this.service.api.user()?.id}:${this.prefix()}`;
  }
  private persistDrafts(): void {
    this.draftCount.set(this.drafts.size);
    try {
      sessionStorage.setItem(
        this.storageKey(),
        JSON.stringify(
          [...this.drafts].map(([path, content]) => ({
            path,
            content,
            sha: this.loaded.get(path)?.sha ?? '',
          })),
        ),
      );
    } catch {
      this.notice.set(
        'Черновик доступен только до закрытия страницы: хранилище браузера недоступно.',
      );
    }
  }
  private restoreDrafts(): void {
    try {
      const value: unknown = JSON.parse(sessionStorage.getItem(this.storageKey()) ?? '[]');
      if (!Array.isArray(value)) return;
      for (const item of value) {
        if (
          !item ||
          typeof item !== 'object' ||
          !('path' in item) ||
          !('content' in item) ||
          !('sha' in item)
        )
          continue;
        if (
          typeof item.path !== 'string' ||
          typeof item.content !== 'string' ||
          typeof item.sha !== 'string'
        )
          continue;
        this.drafts.set(item.path, item.content);
        this.loaded.set(item.path, {
          path: item.path,
          sha: item.sha,
          commit_sha: '',
          content: item.content,
        });
      }
      this.draftCount.set(this.drafts.size);
      if (this.drafts.size) this.notice.set('Восстановлены несохранённые изменения этой вкладки');
    } catch {
      this.notice.set('Не удалось восстановить черновики этой вкладки.');
    }
  }
  async reloadCurrent(): Promise<void> {
    if (
      this.busy() ||
      this.uploading() ||
      this.fileLoading() ||
      this.recovering() ||
      this.auto.saving()
    )
      return;
    this.refreshRequested.emit();
    const path = this.selected();
    await this.select(path, true);
  }
  downloadDrafts(): void {
    const content = JSON.stringify(
      this.autosave()
        ? [...this.auto.drafts().values()].map(({ path, content }) => ({ path, content }))
        : [...this.drafts].map(([path, content]) => ({ path, content })),
      null,
      2,
    );
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'file-drafts.json';
    link.click();
    URL.revokeObjectURL(url);
  }
  async discardDrafts(): Promise<void> {
    if (
      this.busy() ||
      this.uploading() ||
      this.fileLoading() ||
      this.recovering() ||
      this.auto.saving() ||
      !window.confirm(
        'Заменить все несохранённые файлы серверными версиями? Сначала скачайте нужные черновики.',
      )
    )
      return;
    if (this.autosave()) this.auto.discard();
    else this.drafts.clear();
    this.loaded.clear();
    this.selectedFileReady.set(false);
    this.persistDrafts();
    await this.reloadFiles();
  }
}
