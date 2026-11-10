import { computed, inject, Injectable, signal } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { LearningService, object, RemoteFile, text } from './learning.service';
import { uncertainMutation } from './mutation-recovery';

interface Draft {
  kind?: 'create';
  path: string;
  content: string;
  sha: string;
  savedContent?: string;
}

export interface RepositoryWriteContext {
  enqueue<T>(write: () => Promise<T>): Promise<T>;
  versions(): string;
  completed(): Promise<void>;
}

interface WriteIntent {
  kind: 'save' | 'create' | 'delete';
  path: string;
  sha: string;
  content: string;
  key: string;
  url?: string;
}

export interface ProjectFileWrite {
  kind: WriteIntent['kind'];
  path: string;
  file?: RemoteFile;
}

/** One project, one ordered writer. Drafts and exact pending requests survive reloads. */
@Injectable()
export class ProjectAutosaveService {
  private readonly learning = inject(LearningService);
  readonly drafts = signal<ReadonlyMap<string, Draft>>(new Map());
  readonly pending = signal<WriteIntent | null>(null);
  readonly saving = signal(false);
  readonly error = signal('');
  readonly storageError = signal('');
  readonly lastWrite = signal<ProjectFileWrite | null>(null);
  readonly unresolved = computed(() => this.drafts().size > 0 || !!this.pending() || this.saving());
  readonly status = computed(() =>
    this.error()
      ? 'Не удалось сохранить'
      : this.saving() || this.drafts().size > 0
        ? 'Сохраняем…'
        : 'Сохранено',
  );
  private readonly saved = new Map<string, RemoteFile>();
  private readonly revisions = new Map<string, number>();
  private prefix = '';
  private context: RepositoryWriteContext | undefined;
  private owner = '';
  private scope = 0;
  private readOnly = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running: Promise<boolean> | null = null;

  configure(prefix: string, context?: RepositoryWriteContext): void {
    this.stop();
    this.prefix = prefix;
    this.context = context;
    this.owner = this.currentOwner();
    this.saved.clear();
    this.revisions.clear();
    this.drafts.set(new Map());
    this.pending.set(null);
    this.lastWrite.set(null);
    this.error.set('');
    this.storageError.set('');
    this.saving.set(false);
    this.readOnly = false;
    this.restore();
  }

  setReadOnly(value: boolean): void {
    this.readOnly = value;
    if (value) this.cancelTimer();
  }

  stop(): void {
    this.cancelTimer();
    this.scope++;
    this.running = null;
  }

  content(path: string): string | undefined {
    return this.drafts().get(path)?.content;
  }

  revision(path: string): number {
    return this.revisions.get(path) ?? 0;
  }

  baseline(path: string): RemoteFile | undefined {
    return this.saved.get(path);
  }

  accept(file: RemoteFile, readRevision: number): string {
    // A read started before a successful write must not restore its old contents.
    if (readRevision !== this.revision(file.path))
      return this.content(file.path) ?? this.saved.get(file.path)?.content ?? file.content ?? '';
    const draft = this.drafts().get(file.path);
    if (!draft) this.saved.set(file.path, file);
    else if (!draft.kind && !this.pending() && file.content === draft.content) {
      this.saved.set(file.path, file);
      this.updateDraft(file.path, null);
    } else if (file.sha === draft.sha) {
      this.saved.set(file.path, file);
      this.updateDraft(file.path, { ...draft, savedContent: file.content });
    }
    return this.content(file.path) ?? file.content ?? '';
  }

  edit(file: RemoteFile, content: string): void {
    if (this.readOnly || (this.pending() && this.pending()?.kind !== 'save')) return;
    const previous = this.drafts().get(file.path);
    const saved = this.saved.get(file.path) ?? file;
    const pending = this.pending();
    // Undo can remove a draft only when no different version is being committed.
    const clean = !previous?.kind && content === saved.content && pending?.path !== file.path;
    this.updateDraft(
      file.path,
      clean
        ? null
        : {
            ...(previous?.kind ? { kind: previous.kind } : {}),
            path: file.path,
            content,
            sha: previous?.sha ?? saved.sha,
            savedContent: previous?.savedContent ?? saved.content,
          },
    );
    this.schedule();
  }

  schedule(): void {
    this.cancelTimer();
    if (this.readOnly || this.error() || !this.unresolved()) return;
    this.timer = setTimeout(() => void this.flush(), 800);
  }

  async flush(retry = false): Promise<boolean> {
    this.cancelTimer();
    if (this.running) return this.running;
    if (!this.isCurrent(this.scope) || (this.error() && !retry)) return false;
    if (retry) this.error.set('');
    const scope = this.scope;
    const task = this.drain(scope);
    this.running = task;
    try {
      return await task;
    } finally {
      if (scope === this.scope) this.running = null;
    }
  }

  async create(path: string, content = ''): Promise<boolean> {
    return this.structural({
      kind: 'create',
      path,
      sha: '',
      content,
      key: crypto.randomUUID(),
    });
  }

  async remove(file: RemoteFile): Promise<boolean> {
    return this.structural({
      kind: 'delete',
      path: file.path,
      sha: file.sha,
      content: '',
      key: crypto.randomUUID(),
    });
  }

  replace(file: RemoteFile, readRevision: number): boolean {
    if (
      readRevision !== this.revision(file.path) ||
      this.pending()?.path === file.path ||
      this.saving()
    )
      return false;
    this.saved.set(file.path, file);
    this.updateDraft(file.path, null);
    this.error.set('');
    this.schedule();
    return true;
  }

  discard(): void {
    if (this.pending() || this.saving()) return;
    this.drafts.set(new Map());
    this.error.set('');
    this.persist();
  }

  private async structural(intent: WriteIntent): Promise<boolean> {
    const scope = this.scope;
    if (this.readOnly || !(await this.flush()) || !this.isCurrent(scope)) return false;
    if (intent.kind === 'delete') intent.sha = this.saved.get(intent.path)?.sha ?? intent.sha;
    if (intent.kind === 'create')
      this.updateDraft(intent.path, {
        kind: 'create',
        path: intent.path,
        content: intent.content,
        sha: '',
      });
    this.pending.set(intent);
    this.persist();
    return this.flush();
  }

  private async drain(scope: number): Promise<boolean> {
    while (this.isCurrent(scope)) {
      let intent = this.pending();
      if (!intent) {
        if (this.readOnly) return this.drafts().size === 0;
        const draft = this.drafts().values().next().value;
        if (!draft) return true;
        intent = { ...draft, kind: draft.kind ?? 'save', key: crypto.randomUUID() };
        this.pending.set(intent);
        this.persist();
      }
      this.saving.set(true);
      try {
        const current = intent;
        const write = async () => {
          if (!current.url) {
            const query = [
              this.context?.versions() ?? '',
              current.kind === 'create' ? '' : 'path=' + encodeURIComponent(current.path),
              current.kind === 'delete' ? 'sha=' + encodeURIComponent(current.sha) : '',
            ]
              .filter(Boolean)
              .join('&');
            current.url =
              this.prefix +
              (current.kind === 'create' ? '/files' : '/files/content') +
              (query ? '?' + query : '');
            this.pending.set({ ...current });
            this.persist();
          }
          const method =
            current.kind === 'save' ? 'PUT' : current.kind === 'create' ? 'POST' : 'DELETE';
          const body =
            current.kind === 'delete'
              ? undefined
              : current.kind === 'create'
                ? { path: current.path, content: current.content }
                : { sha: current.sha, content: current.content };
          try {
            const response = await this.learning.api.request(
              method,
              current.url,
              body,
              current.key,
              100000,
            );
            await this.context?.completed();
            return response;
          } catch (error) {
            if (error instanceof HttpErrorResponse && error.status === 409)
              await this.context?.completed();
            throw error;
          }
        };
        const response = await (this.context ? this.context.enqueue(write) : write());
        if (!this.isCurrent(scope)) return false;
        const file = intent.kind === 'delete' ? undefined : this.parseFile(response, intent);
        this.apply(intent, file);
        this.pending.set(null);
        this.persist();
        this.lastWrite.set({ kind: intent.kind, path: intent.path, file });
      } catch (error) {
        if (!this.isCurrent(scope)) return false;
        // A malformed success response may still have committed the write.
        if (error instanceof HttpErrorResponse && !this.uncertainWrite(error))
          this.pending.set(null);
        this.error.set(this.message(error));
        this.persist();
        return false;
      } finally {
        if (scope === this.scope) this.saving.set(false);
      }
    }
    return false;
  }

  private parseFile(value: unknown, intent: WriteIntent): RemoteFile {
    const response = object(value);
    const path = text(response['path']);
    const sha = text(response['sha']);
    const commit = text(response['commit_sha']);
    if (path !== intent.path || !sha || !commit) throw new Error('Некорректный ответ сервера.');
    return {
      path,
      sha,
      commit_sha: commit,
      content: intent.content,
      size: new TextEncoder().encode(intent.content).length,
    };
  }

  private apply(intent: WriteIntent, file: RemoteFile | undefined): void {
    this.revisions.set(intent.path, this.revision(intent.path) + 1);
    const drafts = new Map(this.drafts());
    const draft = drafts.get(intent.path);
    if (!file) {
      this.saved.delete(intent.path);
      drafts.delete(intent.path);
    } else {
      this.saved.set(file.path, file);
      if (draft && draft.content !== intent.content)
        drafts.set(file.path, { ...draft, sha: file.sha, savedContent: intent.content });
      else drafts.delete(file.path);
    }
    this.drafts.set(drafts);
  }

  private updateDraft(path: string, draft: Draft | null): void {
    const drafts = new Map(this.drafts());
    if (draft) drafts.set(path, draft);
    else drafts.delete(path);
    this.drafts.set(drafts);
    this.persist();
  }

  private uncertainWrite(error: unknown): boolean {
    return (
      uncertainMutation(error) ||
      (error instanceof HttpErrorResponse &&
        error.status === 409 &&
        error.error?.code === 'OPERATION_NEEDS_ATTENTION')
    );
  }

  private message(error: unknown): string {
    if (error instanceof HttpErrorResponse && error.status === 409 && !this.uncertainWrite(error))
      return 'Файл изменён в другой вкладке. Скачайте свой черновик или загрузите текущую версию.';
    if (this.uncertainWrite(error))
      return 'Не удалось сохранить. Ваши изменения остались в черновике.';
    return this.learning.message(error);
  }

  private currentOwner(): string {
    return this.prefix.startsWith('/api/v1/admin/')
      ? this.learning.api.adminUserId()
      : (this.learning.api.user()?.id ?? '');
  }

  private isCurrent(scope: number): boolean {
    return scope === this.scope && !!this.owner && this.owner === this.currentOwner();
  }

  private cancelTimer(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }

  private storageKey(): string {
    return `prodguild.project-autosave.v1:${this.owner}:${this.prefix}`;
  }

  private persist(): void {
    try {
      sessionStorage.setItem(
        this.storageKey(),
        JSON.stringify({ drafts: [...this.drafts().values()], pending: this.pending() }),
      );
      this.storageError.set('');
    } catch {
      this.storageError.set(
        'Черновик не сохранён в браузере. Не закрывайте страницу до сохранения.',
      );
    }
  }

  private restore(): void {
    try {
      const saved = sessionStorage.getItem(this.storageKey());
      const legacy = `prodguild.remote-drafts.v1:${this.owner}:${this.prefix}`;
      const value: unknown = JSON.parse(
        saved ?? '{"drafts":' + (sessionStorage.getItem(legacy) ?? '[]') + '}',
      );
      const data = object(value);
      const drafts = new Map<string, Draft>();
      if (Array.isArray(data['drafts'])) {
        for (const value of data['drafts']) {
          const draft = object(value);
          const path = text(draft['path']);
          drafts.set(path, {
            ...(draft['kind'] === 'create' ? { kind: 'create' as const } : {}),
            path,
            content: text(draft['content']),
            sha: text(draft['sha']),
            ...(typeof draft['savedContent'] === 'string'
              ? { savedContent: draft['savedContent'] }
              : {}),
          });
        }
      }
      this.drafts.set(drafts);
      if ([...drafts.values()].some(draft => draft.kind === 'create'))
        this.error.set('Создание файла не подтверждено. Черновик сохранён; повторите попытку.');
      if (data['pending']) {
        const pending = object(data['pending']);
        const kind = pending['kind'];
        if (kind !== 'save' && kind !== 'create' && kind !== 'delete')
          throw new Error('Invalid intent');
        this.pending.set({
          kind,
          path: text(pending['path']),
          sha: text(pending['sha']),
          content: text(pending['content']),
          key: text(pending['key']),
          ...(typeof pending['url'] === 'string' ? { url: pending['url'] } : {}),
        });
        // Older sessions kept upload contents only in their pending request.
        const intent = this.pending();
        if (intent?.kind === 'create' && !drafts.has(intent.path)) {
          drafts.set(intent.path, {
            kind: 'create',
            path: intent.path,
            content: intent.content,
            sha: '',
          });
          this.drafts.set(new Map(drafts));
        }
        this.error.set('Сохранение не подтверждено. Повторите попытку.');
      }
    } catch {
      this.storageError.set(
        'Не удалось восстановить черновик. Сохранённые файлы доступны на сервере.',
      );
    }
  }
}
