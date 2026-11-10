import { HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BackendService } from './backend.service';
import { object, text } from './learning.service';

export interface Attachment {
  id: string;
  path: string;
  size: number;
  media_type: string;
  text: boolean;
  sha256: string;
  download_url: string;
}

export interface AttachmentCollection {
  version: number;
  items: Attachment[];
  total_bytes: number;
  max_file_bytes: number;
  max_total_bytes: number;
  max_files: number;
}

export interface AttachmentMutation {
  method: 'POST' | 'PUT' | 'DELETE';
  url: string;
  body?: Blob;
  key: string;
  uploadId?: string;
  rejected?: boolean;
}

export interface AttachmentUpload {
  id: string;
  path: string;
  mediaType: string;
  body: Blob;
}

function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    throw new Error('Сервер вернул некорректные данные приложений.');
  return value;
}

function localUrl(value: unknown): string {
  const url = text(value);
  if (!url.startsWith('/api/v1/') || /[\\\r\n]/.test(url))
    throw new Error('Сервер вернул некорректный адрес файла.');
  return url;
}

@Injectable({ providedIn: 'root' })
export class AttachmentService {
  readonly api = inject(BackendService);
  private readonly tabId = (() => {
    const key = 'prodguild.attachment-tab.v1';
    try {
      const existing = sessionStorage.getItem(key);
      if (existing) return existing;
      const id = crypto.randomUUID();
      sessionStorage.setItem(key, id);
      return id;
    } catch {
      return crypto.randomUUID();
    }
  })();

  recoveryScope(prefix: string): string {
    return `${this.api.user()?.id ?? ''}:${this.tabId}:${prefix}`;
  }

  async list(prefix: string): Promise<AttachmentCollection> {
    return this.collection(await this.api.request('GET', prefix));
  }

  async mutate(intent: AttachmentMutation): Promise<AttachmentCollection> {
    const value = await this.api.request(
      intent.method,
      localUrl(intent.url),
      intent.body ? new Blob([intent.body], { type: 'application/octet-stream' }) : undefined,
      intent.key,
      60_000,
    );
    try {
      return this.collection(value);
    } catch {
      throw new HttpErrorResponse({ status: 200, error: 'Invalid attachment response' });
    }
  }

  private async database(): Promise<IDBDatabase> {
    return new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('prodguild.attachment-recovery.v1', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('requests');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new Error('Хранилище восстановления недоступно.'));
    });
  }

  private async record(key: string, value?: unknown): Promise<unknown> {
    const db = await this.database();
    try {
      return await new Promise<unknown>((resolve, reject) => {
        const transaction = db.transaction(
          'requests',
          value === undefined ? 'readonly' : 'readwrite',
        );
        const store = transaction.objectStore('requests');
        const request =
          value === undefined
            ? store.get(key)
            : value === null
              ? store.delete(key)
              : store.put(value, key);
        let result: unknown;
        request.onsuccess = () => {
          result = request.result;
        };
        transaction.oncomplete = () => resolve(result);
        transaction.onerror = transaction.onabort = () =>
          reject(new Error('Не удалось сохранить запрос для повтора.'));
      });
    } finally {
      db.close();
    }
  }

  async recovery(
    scope: string,
    intent?: AttachmentMutation | null,
  ): Promise<AttachmentMutation | null> {
    const value = await this.record(scope, intent);
    if (
      intent !== undefined ||
      !value ||
      typeof value !== 'object' ||
      !('method' in value) ||
      !('url' in value) ||
      !('key' in value)
    )
      return null;
    if (
      (value.method !== 'POST' && value.method !== 'PUT' && value.method !== 'DELETE') ||
      typeof value.url !== 'string' ||
      typeof value.key !== 'string'
    )
      return null;
    const body = 'body' in value && value.body instanceof Blob ? value.body : undefined;
    if (value.method !== 'DELETE' && !body)
      throw new Error('Не удалось восстановить содержимое запроса.');
    return {
      method: value.method,
      url: localUrl(value.url),
      key: value.key,
      body,
      ...('uploadId' in value && typeof value.uploadId === 'string'
        ? { uploadId: value.uploadId }
        : {}),
      ...('rejected' in value && value.rejected === true ? { rejected: true } : {}),
    };
  }

  async uploads(scope: string, files?: readonly AttachmentUpload[]): Promise<AttachmentUpload[]> {
    const value = await this.record(scope + ':uploads', files);
    if (files !== undefined || !Array.isArray(value)) return [];
    return value.filter(
      (item): item is AttachmentUpload =>
        !!item &&
        typeof item === 'object' &&
        typeof item.id === 'string' &&
        typeof item.path === 'string' &&
        typeof item.mediaType === 'string' &&
        item.body instanceof Blob,
    );
  }

  /** Removing the queued bytes and replay intent together prevents duplicate uploads after a crash. */
  async completeRecovery(scope: string, uploadId?: string): Promise<void> {
    const db = await this.database();
    try {
      await new Promise<void>((resolve, reject) => {
        const transaction = db.transaction('requests', 'readwrite');
        const store = transaction.objectStore('requests');
        if (uploadId) {
          const request = store.get(scope + ':uploads');
          request.onsuccess = () => {
            const value: unknown = request.result;
            if (Array.isArray(value))
              store.put(
                value.filter(
                  item =>
                    !item || typeof item !== 'object' || !('id' in item) || item.id !== uploadId,
                ),
                scope + ':uploads',
              );
          };
        }
        store.delete(scope);
        transaction.oncomplete = () => resolve();
        transaction.onerror = transaction.onabort = () =>
          reject(new Error('Не удалось завершить восстановление загрузки.'));
      });
    } finally {
      db.close();
    }
  }

  async content(file: Attachment): Promise<Blob> {
    const url = localUrl(file.download_url);
    return this.api.download(url);
  }

  async download(file: Attachment, draft?: string): Promise<void> {
    const blob = draft === undefined ? await this.content(file) : new Blob([draft]);
    this.save(blob, file.path);
  }

  save(blob: Blob, path: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = path.split('/').at(-1) || 'file';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private collection(value: unknown): AttachmentCollection {
    const data = object(value);
    if (!Array.isArray(data['items'])) throw new Error('Не удалось прочитать список приложений.');
    return {
      version: integer(data['version']),
      total_bytes: integer(data['total_bytes']),
      max_file_bytes: integer(data['max_file_bytes']),
      max_total_bytes: integer(data['max_total_bytes']),
      max_files: integer(data['max_files']),
      items: data['items'].map((item: unknown) => {
        const file = object(item);
        if (typeof file['text'] !== 'boolean') throw new Error('Не удалось прочитать тип файла.');
        return {
          id: text(file['id']),
          path: text(file['path']),
          size: integer(file['size']),
          media_type: text(file['media_type']),
          text: file['text'],
          sha256: text(file['sha256']),
          download_url: localUrl(file['download_url']),
        };
      }),
    };
  }
}
