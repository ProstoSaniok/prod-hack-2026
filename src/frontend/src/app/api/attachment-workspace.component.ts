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
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { TuiButton, TuiTextfield } from '@taiga-ui/core';
import {
  AttachmentCollection,
  AttachmentMutation,
  AttachmentUpload,
  AttachmentService,
} from './attachment.service';
import { RepositoryEditorComponent } from '../cases/solutions/repository-editor/repository-editor.component';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { LeavePageService } from '../shared/leave-page.service';
import { WorkspaceMaximizeDirective } from '../shared/workspace-maximize/workspace-maximize.directive';
import { ProductIconComponent } from '../shared/product-icon.component';
import { uncertainMutation } from './mutation-recovery';

interface TextDraft {
  content: string;
  sha256: string;
}

@Component({
  selector: 'app-attachment-workspace',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    TuiButton,
    TuiTextfield,
    RepositoryEditorComponent,
    LoadingStateComponent,
    ProductIconComponent,
  ],
  hostDirectives: [WorkspaceMaximizeDirective],
  templateUrl: './attachment-workspace.component.html',
  styleUrl: './attachment-workspace.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AttachmentWorkspaceComponent {
  readonly service = inject(AttachmentService);
  readonly maximize = inject(WorkspaceMaximizeDirective);
  private readonly destroy = inject(DestroyRef);
  readonly prefix = input.required<string>();
  readonly title = input('Приложения к отзыву');
  readonly readOnly = input(false);
  readonly disabled = input(false);
  readonly allowMaximize = input(true);
  readonly beforeWrite = input<() => Promise<boolean>>(async () => true);
  readonly versionChanged = output<number>();
  readonly loaded = output<number>();
  readonly changed = output<void>();
  readonly unsettled = output<boolean>();
  readonly working = output<boolean>();
  readonly collection = signal<AttachmentCollection | null>(null);
  readonly loading = signal(false);
  readonly fileLoading = signal(false);
  readonly busy = signal(false);
  readonly processing = signal(false);
  readonly importing = signal(false);
  readonly activeWork = computed(() => this.busy() || this.processing() || this.importing());
  readonly uploads = signal<readonly AttachmentUpload[]>([]);
  readonly error = signal('');
  readonly notice = signal('');
  readonly storageNotice = signal('');
  readonly creating = signal(false);
  readonly selected = signal('');
  readonly content = signal('');
  readonly pending = signal<AttachmentMutation | null>(null);
  readonly drafts = signal<ReadonlyMap<string, TextDraft>>(new Map());
  readonly newPath = new FormControl('', { nonNullable: true, validators: [Validators.required] });
  readonly inputId = `prodguild-attachment-${crypto.randomUUID()}`;
  readonly files = computed(() => this.collection()?.items ?? []);
  readonly documents = computed(() =>
    this.files().map(file => ({
      id: file.id,
      path: file.path,
      content: this.drafts().get(file.id)?.content ?? this.contents.get(file.id) ?? '',
      modified: this.drafts().has(file.id),
      ...(file.text ? {} : { binary: { size: file.size, mimeType: file.media_type } }),
    })),
  );
  readonly active = computed(() => this.files().find(file => file.id === this.selected()));
  readonly dirty = computed(
    () => this.drafts().size > 0 || !!this.pending() || this.uploads().length > 0,
  );
  readonly status = computed(() =>
    this.activeWork()
      ? 'Сохраняем…'
      : this.dirty()
        ? 'Есть несохранённые изменения'
        : this.notice(),
  );
  private readonly contents = new Map<string, string>();
  private scope = 0;
  private selection = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | null = null;
  private recoveryKey = '';
  private initialized: Promise<void> = Promise.resolve();
  private importsRunning: Promise<void> | null = null;
  private readonly owner = computed(() => this.service.api.user()?.id ?? '');

  constructor() {
    effect(() =>
      this.unsettled.emit(this.loading() || this.activeWork() || this.dirty() || !!this.error()),
    );
    effect(() => this.working.emit(this.activeWork()));
    effect(() => {
      this.prefix();
      this.owner();
      untracked(() => {
        this.initialized = this.openScope();
      });
    });
    this.destroy.onDestroy(
      inject(LeavePageService).register(
        async () => {
          if (!this.dirty() && !this.activeWork()) return true;
          if (this.activeWork()) return false;
          if (await this.flush()) return true;
          return window.confirm('Есть несохранённые приложения. Покинуть страницу?');
        },
        () => this.dirty() || this.activeWork(),
      ),
    );
    this.destroy.onDestroy(() => {
      this.scope++;
      this.cancelTimer();
    });
  }

  private current(scope: number): boolean {
    return !this.destroy.destroyed && scope === this.scope;
  }
  private cancelTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
  }
  private async openScope(): Promise<void> {
    const scope = ++this.scope;
    this.cancelTimer();
    this.selection++;
    this.running = null;
    this.importsRunning = null;
    this.processing.set(false);
    this.importing.set(false);
    this.uploads.set([]);
    this.contents.clear();
    this.collection.set(null);
    this.selected.set('');
    this.content.set('');
    this.drafts.set(new Map());
    this.pending.set(null);
    this.error.set('');
    this.busy.set(false);
    this.fileLoading.set(false);
    this.recoveryKey = this.service.recoveryScope(this.prefix());
    if (!this.readOnly()) this.restoreDrafts();
    this.loading.set(true);
    try {
      const [collection, pending, uploads] = await Promise.all([
        this.service.list(this.prefix()),
        this.readOnly()
          ? Promise.resolve(null)
          : this.service.recovery(this.recoveryKey).catch(() => {
              if (this.current(scope))
                this.storageNotice.set(
                  'Не удалось прочитать восстановление: файлы доступны только до закрытия страницы.',
                );
              return null;
            }),
        this.readOnly()
          ? Promise.resolve([])
          : this.service.uploads(this.recoveryKey).catch(() => {
              if (this.current(scope))
                this.storageNotice.set('Не удалось прочитать сохранённую очередь файлов.');
              return [];
            }),
      ]);
      if (!this.current(scope)) return;
      this.collection.set(collection);
      this.loaded.emit(collection.version);
      this.pending.set(pending);
      this.uploads.set(uploads);
      if (pending?.rejected)
        this.error.set('Загрузка отклонена. Файлы сохранены для явного повтора.');
      else if (pending)
        this.error.set('Предыдущее сохранение не завершено. Повторите его с тем же запросом.');
      else if (uploads.length) this.error.set('Есть несохранённые файлы. Продолжите загрузку.');
      if (collection.items[0]) await this.select(collection.items[0].id);
    } catch (error) {
      if (this.current(scope)) this.error.set(this.service.api.errorMessage(error));
    } finally {
      if (this.current(scope)) this.loading.set(false);
    }
  }

  async reload(): Promise<void> {
    if (this.activeWork() || (this.pending() && !this.pending()?.rejected)) return;
    const scope = this.scope;
    this.loading.set(true);
    this.error.set('');
    try {
      const collection = await this.service.list(this.prefix());
      if (!this.current(scope)) return;
      for (const file of this.files()) {
        if (collection.items.find(item => item.id === file.id)?.sha256 !== file.sha256)
          this.contents.delete(file.id);
      }
      this.collection.set(collection);
      this.loaded.emit(collection.version);
      // A newer server version never silently rebases a locally edited file.
      if (
        [...this.drafts()].some(
          ([id, draft]) => collection.items.find(file => file.id === id)?.sha256 !== draft.sha256,
        )
      )
        this.error.set(
          'Файл изменился на сервере. Скачайте свой черновик и явно загрузите серверную версию.',
        );
      const selected = collection.items.find(file => file.id === this.selected());
      await this.select(selected?.id ?? collection.items[0]?.id ?? '');
    } catch (error) {
      if (this.current(scope)) this.error.set(this.service.api.errorMessage(error));
    } finally {
      if (this.current(scope)) this.loading.set(false);
    }
  }

  acceptVersion(version: number): void {
    this.collection.update(value => (value ? { ...value, version } : value));
  }
  getVersion(): number | null {
    return this.collection()?.version ?? null;
  }

  async select(id: string): Promise<void> {
    const file = this.files().find(item => item.id === id);
    const selection = ++this.selection,
      scope = this.scope;
    this.selected.set(id);
    this.content.set(this.drafts().get(id)?.content ?? this.contents.get(id) ?? '');
    this.fileLoading.set(false);
    if (!file?.text || this.contents.has(id) || this.drafts().has(id)) return;
    this.fileLoading.set(true);
    try {
      const blob = await this.service.content(file);
      if (blob.size > 1_048_576) throw new Error('Этот файл можно только скачать.');
      const content = new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer());
      if (!this.current(scope) || selection !== this.selection) return;
      this.contents.set(id, content);
      this.content.set(content);
    } catch (error) {
      if (this.current(scope)) this.error.set(this.service.api.errorMessage(error));
    } finally {
      if (this.current(scope) && selection === this.selection) this.fileLoading.set(false);
    }
  }

  edit(content: string): void {
    const file = this.active();
    if (
      !file?.text ||
      this.fileLoading() ||
      this.readOnly() ||
      this.disabled() ||
      this.pending() ||
      this.importing()
    )
      return;
    this.content.set(content);
    this.drafts.update(previous => {
      const next = new Map(previous);
      if (content === this.contents.get(file.id)) next.delete(file.id);
      else next.set(file.id, { content, sha256: previous.get(file.id)?.sha256 ?? file.sha256 });
      return next;
    });
    this.persistDrafts();
    this.cancelTimer();
    if (!this.error()) this.timer = setTimeout(() => void this.flush(), 800);
  }

  async flush(retry = false): Promise<boolean> {
    this.cancelTimer();
    const scope = this.scope;
    if (this.importsRunning) await this.importsRunning;
    if (!this.current(scope)) return false;
    if (this.running) return this.running;
    if (this.readOnly()) return !this.dirty();
    if (this.loading() || this.disabled() || (this.error() && !retry)) return false;
    this.processing.set(true);
    const task = this.drain(retry);
    this.running = task;
    try {
      return await task;
    } finally {
      if (this.running === task) {
        this.running = null;
        this.processing.set(false);
      }
    }
  }

  private async drain(retry: boolean): Promise<boolean> {
    if (this.busy()) return false;
    const scope = this.scope;
    if (retry) this.error.set('');
    const pending = this.pending();
    if (pending?.rejected) {
      this.error.set('Загрузка отклонена. Повторите её с выбранным именем или скачайте файлы.');
      return false;
    }
    if (pending && !(await this.write(pending))) return false;
    while (this.current(scope) && this.drafts().size) {
      const entry = this.drafts().entries().next().value;
      if (!entry) break;
      const [id, draft] = entry;
      const file = this.files().find(item => item.id === id);
      if (!file || file.sha256 !== draft.sha256) {
        this.error.set(
          'Файл изменился на сервере. Скачайте свой черновик и явно загрузите серверную версию.',
        );
        return false;
      }
      if (new TextEncoder().encode(draft.content).length > 1_048_576) {
        this.error.set('Редактируемый текст не должен превышать 1 МиБ.');
        return false;
      }
      if (!(await this.prepare()) || !this.current(scope)) return false;
      const content = draft.content;
      if (
        !(await this.write(
          this.intent('PUT', file.path, new Blob([content]), file.id, file.media_type),
        ))
      )
        return false;
      this.contents.set(id, content);
      this.drafts.update(previous => {
        const next = new Map(previous),
          current = next.get(id);
        if (current?.content === content) next.delete(id);
        else if (current)
          next.set(id, {
            ...current,
            sha256: this.files().find(item => item.id === id)?.sha256 ?? current.sha256,
          });
        return next;
      });
      this.persistDrafts();
    }
    while (this.current(scope) && this.uploads().length) {
      const file = this.uploads()[0];
      if (!file || !this.validPath(file.path)) return false;
      const collection = this.collection();
      if (!collection) return false;
      if (this.pathExists(file.path)) {
        this.error.set('Такой путь уже есть. Укажите другое имя для несохранённого файла.');
        return false;
      }
      if (
        file.body.size > collection.max_file_bytes ||
        collection.total_bytes + file.body.size > collection.max_total_bytes ||
        collection.items.length >= collection.max_files
      ) {
        this.error.set('Лимит приложений: 10 МиБ на файл, 50 МиБ и 100 файлов на набор.');
        return false;
      }
      if (!(await this.prepare()) || !this.current(scope)) return false;
      if (
        !(await this.write({
          ...this.intent('POST', file.path, file.body, undefined, file.mediaType),
          uploadId: file.id,
        }))
      )
        return false;
      const saved = this.files().find(item => item.path === file.path);
      if (saved) await this.select(saved.id);
    }
    return this.current(scope) && !this.dirty();
  }

  private async prepare(): Promise<boolean> {
    const scope = this.scope;
    if (!(await this.beforeWrite()()) || !this.current(scope)) return false;
    return !!this.collection() && !this.readOnly() && !this.disabled();
  }

  private intent(
    method: AttachmentMutation['method'],
    path: string,
    body?: Blob,
    id?: string,
    mediaType = 'text/plain',
  ): AttachmentMutation {
    const query = new URLSearchParams({
      expected_version: String(this.collection()?.version ?? 0),
    });
    if (method === 'POST') query.set('path', path);
    if (method !== 'DELETE') query.set('media_type', mediaType);
    return {
      method,
      url: this.prefix() + (id ? '/' + encodeURIComponent(id) : '') + '?' + query,
      body,
      key: crypto.randomUUID(),
    };
  }

  private async write(intent: AttachmentMutation): Promise<boolean> {
    const scope = this.scope,
      recoveryKey = this.recoveryKey;
    this.busy.set(true);
    this.error.set('');
    this.pending.set(intent);
    let durableIntent = true;
    try {
      await this.service.recovery(recoveryKey, intent).catch(() => {
        durableIntent = false;
        if (this.current(scope))
          this.storageNotice.set('Запрос доступен для повтора только до закрытия страницы.');
      });
      const collection = await this.service.mutate(intent);
      try {
        await this.service.completeRecovery(recoveryKey, intent.uploadId);
      } catch {
        if (durableIntent) {
          if (this.current(scope))
            this.error.set(
              'Файл сохранён, но браузер не завершил восстановление. Повторите тот же запрос.',
            );
          return false;
        }
        if (this.current(scope))
          this.storageNotice.set(
            'Хранилище браузера недоступно. Файл сохранён на сервере; восстановление запросов работает только до закрытия страницы.',
          );
      }
      if (!this.current(scope)) return false;
      if (intent.uploadId)
        this.uploads.update(files => files.filter(file => file.id !== intent.uploadId));
      this.collection.set(collection);
      this.pending.set(null);
      if (intent.method === 'PUT' && intent.body) {
        const id = new URL(intent.url, 'https://prodguild.invalid').pathname.split('/').at(-1);
        const saved = collection.items.find(file => file.id === id);
        if (saved?.text) {
          const content = await intent.body.text();
          if (!this.current(scope)) return false;
          this.contents.set(saved.id, content);
          this.drafts.update(previous => {
            const next = new Map(previous),
              draft = next.get(saved.id);
            if (draft?.content === content) next.delete(saved.id);
            else if (draft) next.set(saved.id, { ...draft, sha256: saved.sha256 });
            return next;
          });
          this.persistDrafts();
        }
      }
      this.versionChanged.emit(collection.version);
      this.changed.emit();
      this.notice.set('Сохранено на сервере');
      return true;
    } catch (error) {
      if (this.current(scope)) {
        this.error.set(this.service.api.errorMessage(error));
        if (!uncertainMutation(error)) {
          if (intent.method === 'POST' && intent.body) {
            const rejected = { ...intent, rejected: true };
            this.pending.set(rejected);
            await this.service.recovery(recoveryKey, rejected).catch(() => {
              if (this.current(scope))
                this.storageNotice.set('Несохранённые файлы доступны только до закрытия страницы.');
            });
          } else {
            this.pending.set(null);
            await this.service.recovery(recoveryKey, null).catch(() => undefined);
          }
        }
      }
      return false;
    } finally {
      if (this.current(scope)) this.busy.set(false);
    }
  }

  async create(): Promise<void> {
    const path = this.newPath.value.trim();
    if (!path || !this.validPath(path) || !(await this.flush()) || !(await this.prepare())) return;
    if (this.files().some(file => file.path === path)) {
      this.error.set('Такой файл уже есть. Укажите другое имя.');
      return;
    }
    if (await this.write(this.intent('POST', path, new Blob([''])))) {
      this.creating.set(false);
      this.newPath.reset();
      const file = this.files().find(item => item.path === path);
      if (file) {
        this.contents.set(file.id, '');
        await this.select(file.id);
      }
    }
  }

  async upload(event: Event): Promise<void> {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const files = [...(target.files ?? [])];
    target.value = '';
    await this.importFiles(files);
  }

  async importFiles(files: readonly File[]): Promise<void> {
    if (!files.length || this.readOnly() || this.disabled()) return;
    const scope = this.scope,
      previous = this.importsRunning ?? Promise.resolve();
    this.importing.set(true);
    const task = (async (): Promise<void> => {
      await previous;
      if (this.running) await this.running;
      await this.initialized;
      if (!this.current(scope)) return;
      const queued = this.uploads();
      if (
        files.some(file => file.size > 10 * 1024 * 1024) ||
        queued.length + files.length > 100 ||
        [...queued.map(file => file.body), ...files].reduce((total, file) => total + file.size, 0) >
          50 * 1024 * 1024
      ) {
        this.error.set('Выберите не более 100 файлов, до 10 МиБ каждый и 50 МиБ суммарно.');
        return;
      }
      this.uploads.set([
        ...queued,
        ...files.map(file => ({
          id: crypto.randomUUID(),
          path: file.name,
          mediaType: file.type || 'application/octet-stream',
          body: file,
        })),
      ]);
      await this.persistUploads();
    })();
    this.importsRunning = task;
    try {
      await task;
    } finally {
      if (this.importsRunning === task) {
        this.importsRunning = null;
        this.importing.set(false);
      }
    }
    if (this.current(scope)) await this.flush();
  }

  private pathExists(path: string): boolean {
    return this.files().some(
      file =>
        file.path === path || file.path.startsWith(path + '/') || path.startsWith(file.path + '/'),
    );
  }

  private async persistUploads(): Promise<void> {
    const scope = this.scope;
    await this.service.uploads(this.recoveryKey, this.uploads()).catch(() => {
      if (this.current(scope))
        this.storageNotice.set('Несохранённые файлы доступны только до закрытия страницы.');
    });
  }

  async retryUpload(): Promise<void> {
    if (this.activeWork() || this.loading() || this.readOnly() || this.disabled()) return;
    const pending = this.pending();
    if (pending && !pending.rejected) return;
    const file = this.uploads()[0];
    const body = pending?.body ?? file?.body;
    if (!body) return;
    const scope = this.scope;
    await this.reload();
    if (!this.current(scope) || !this.collection()) return;
    const oldPath = pending
      ? (new URL(pending.url, 'https://prodguild.invalid').searchParams.get('path') ?? 'file')
      : (file?.path ?? 'file');
    const path = window.prompt('Имя несохранённого приложения для загрузки:', oldPath)?.trim();
    if (!path || !this.validPath(path)) return;
    if (this.pathExists(path)) {
      this.error.set('Такой путь уже есть. Выберите другое имя.');
      return;
    }
    this.processing.set(true);
    try {
      if (!(await this.prepare()) || !this.current(scope)) return;
      const uploadId = pending?.uploadId ?? file?.id;
      if (uploadId) {
        this.uploads.update(files =>
          files.map(item => (item.id === uploadId ? { ...item, path } : item)),
        );
        await this.persistUploads();
      }
      if (!this.current(scope)) return;
      const mediaType = pending
        ? (new URL(pending.url, 'https://prodguild.invalid').searchParams.get('media_type') ??
          'application/octet-stream')
        : (file?.mediaType ?? 'application/octet-stream');
      if (
        !(await this.write({ ...this.intent('POST', path, body, undefined, mediaType), uploadId }))
      )
        return;
    } finally {
      if (this.current(scope)) this.processing.set(false);
    }
    if (this.current(scope)) await this.flush(true);
  }

  downloadUploads(): void {
    for (const file of this.uploads()) this.service.save(file.body, file.path);
    const pending = this.pending();
    if (pending?.body && !pending.uploadId && pending.method === 'POST')
      this.service.save(
        pending.body,
        new URL(pending.url, 'https://prodguild.invalid').searchParams.get('path') ?? 'file',
      );
  }

  async discardUploads(): Promise<void> {
    if (
      this.activeWork() ||
      this.loading() ||
      (this.pending() && !this.pending()?.rejected) ||
      !window.confirm('Отменить несохранённые загрузки? Сначала скачайте нужные файлы.')
    )
      return;
    this.uploads.set([]);
    this.pending.set(null);
    await this.persistUploads();
    await this.service.recovery(this.recoveryKey, null).catch(() => undefined);
    this.error.set('');
  }

  async remove(): Promise<void> {
    const file = this.active();
    if (
      !file ||
      !window.confirm(`Удалить приложение «${file.path}»?`) ||
      !(await this.flush()) ||
      !(await this.prepare())
    )
      return;
    if (await this.write(this.intent('DELETE', file.path, undefined, file.id))) {
      this.contents.delete(file.id);
      await this.select(this.files()[0]?.id ?? '');
    }
  }

  async download(): Promise<void> {
    const file = this.active();
    if (!file) return;
    try {
      await this.service.download(file, this.drafts().get(file.id)?.content);
    } catch (error) {
      this.error.set(this.service.api.errorMessage(error));
    }
  }

  downloadDrafts(): void {
    for (const [id, draft] of this.drafts()) {
      const path = this.files().find(file => file.id === id)?.path ?? `${id}.txt`;
      this.service.save(new Blob([draft.content]), path);
    }
  }

  async discard(): Promise<void> {
    if (
      this.activeWork() ||
      this.pending() ||
      !window.confirm('Заменить несохранённый текст серверной версией?')
    )
      return;
    this.drafts.set(new Map());
    this.persistDrafts();
    this.contents.clear();
    await this.reload();
    await this.select(this.selected());
  }

  private validPath(path: string): boolean {
    if (
      !path ||
      new TextEncoder().encode(path).length > 1024 ||
      path.includes('\\') ||
      [...path].some(
        character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
      ) ||
      path
        .split('/')
        .some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')
    ) {
      this.error.set('Укажите корректное имя файла или путь внутри папки.');
      return false;
    }
    return true;
  }
  private persistDrafts(): void {
    try {
      sessionStorage.setItem(
        'prodguild.attachment-drafts:' + this.recoveryKey,
        JSON.stringify([...this.drafts()]),
      );
    } catch {
      this.storageNotice.set(
        'Текст доступен только до закрытия страницы: хранилище браузера недоступно.',
      );
    }
  }
  private restoreDrafts(): void {
    try {
      const value: unknown = JSON.parse(
        sessionStorage.getItem('prodguild.attachment-drafts:' + this.recoveryKey) ?? '[]',
      );
      if (!Array.isArray(value)) return;
      const drafts = new Map<string, TextDraft>();
      for (const entry of value) {
        if (!Array.isArray(entry) || typeof entry[0] !== 'string') continue;
        const draft: unknown = entry[1];
        if (
          draft &&
          typeof draft === 'object' &&
          'content' in draft &&
          'sha256' in draft &&
          typeof draft.content === 'string' &&
          typeof draft.sha256 === 'string'
        )
          drafts.set(entry[0], { content: draft.content, sha256: draft.sha256 });
      }
      this.drafts.set(drafts);
    } catch {
      this.storageNotice.set('Не удалось восстановить несохранённый текст приложений.');
    }
  }
}
