import { Injectable, inject, signal } from '@angular/core';
import { BackendService } from './backend.service';
import { object, text } from './learning.service';
import { uncertainMutation } from './mutation-recovery';

interface PendingWorkflow {
  method: 'POST' | 'DELETE';
  path: string;
  body: unknown;
  key: string;
}

/** Exact intentions survive a reload of this account's current browser session. */
@Injectable({ providedIn: 'root' })
export class WorkflowMutationsService {
  private readonly api = inject(BackendService);
  private readonly memory = new Map<string, PendingWorkflow>();
  private readonly completed = new Set<string>();
  readonly storageNotice = signal('');

  private storageKey(action: string): string {
    const owner = this.api.user()?.id;
    const session = this.api.sessionScope();
    if (!owner || !session) throw new Error('Войдите в аккаунт.');
    return `prodguild.workflow.v1.${session}.${owner}.${action}`;
  }

  pending(action: string): boolean {
    return this.read(action) !== null;
  }

  private read(action: string): PendingWorkflow | null {
    if (!this.api.user() || !this.api.sessionScope()) return null;
    const key = this.storageKey(action);
    if (this.completed.has(key)) return null;
    const memory = this.memory.get(key);
    if (memory) return memory;
    try {
      const stored = sessionStorage.getItem(key);
      if (!stored) return null;
      const value = object(JSON.parse(stored));
      if (value['method'] !== 'POST' && value['method'] !== 'DELETE') return null;
      const pending: PendingWorkflow = {
        method: value['method'],
        path: text(value['path']),
        body: value['body'],
        key: text(value['key']),
      };
      const id = encodeURIComponent(action.slice(action.indexOf(':') + 1));
      const allowed = action.startsWith('application:')
        ? pending.method === 'POST' && pending.path === '/api/v1/reviewer-applications'
        : action.startsWith('assessment:')
          ? pending.method === 'POST' &&
            pending.path === `/api/v1/reviewer-applications/${id}/assessments`
          : action.startsWith('priority:') &&
            ((pending.method === 'POST' &&
              pending.path === `/api/v1/submissions/${id}/priority-requests`) ||
              (pending.method === 'DELETE' &&
                /^\/api\/v1\/priority-requests\/[a-z0-9-]+$/i.test(pending.path)));
      if (!allowed) return null;
      this.memory.set(key, pending);
      return pending;
    } catch {
      this.storageNotice.set('Восстановление запроса после обновления страницы недоступно.');
      return null;
    }
  }

  pendingBody(action: string): unknown {
    return this.read(action)?.body ?? null;
  }

  async mutate(
    action: string,
    method: 'POST' | 'DELETE',
    path: string,
    body: unknown = null,
  ): Promise<unknown> {
    const original = this.read(action);
    if (original) {
      if (
        JSON.stringify([method, path, body]) !==
        JSON.stringify([original.method, original.path, original.body])
      )
        throw new Error('Сначала повторите незавершённый запрос с исходными данными.');
      return this.send(action, original);
    }
    const pending: PendingWorkflow = { method, path, body, key: crypto.randomUUID() };
    const key = this.storageKey(action);
    this.completed.delete(key);
    this.memory.set(key, pending);
    try {
      sessionStorage.setItem(key, JSON.stringify(pending));
    } catch {
      this.storageNotice.set(
        'Повтор запроса доступен до закрытия страницы: хранилище браузера недоступно.',
      );
    }
    return this.send(action, pending);
  }

  async retry(action: string): Promise<unknown> {
    const pending = this.read(action);
    if (!pending) throw new Error('Незавершённого запроса нет. Обновите данные.');
    return this.send(action, pending);
  }

  private async send(action: string, pending: PendingWorkflow): Promise<unknown> {
    const key = this.storageKey(action);
    try {
      const result = await this.api.request(
        pending.method,
        pending.path,
        pending.body,
        pending.key,
      );
      this.clear(key);
      return result;
    } catch (error) {
      if (!uncertainMutation(error)) this.clear(key);
      throw error;
    }
  }

  private clear(key: string): void {
    this.memory.delete(key);
    this.completed.add(key);
    try {
      sessionStorage.removeItem(key);
    } catch {
      this.storageNotice.set('Не удалось обновить сохранённый запрос в браузере.');
    }
  }
}
