import { Injectable, signal } from '@angular/core';
import {
  ReviewDraft,
  ReviewExample,
  ReviewInputError,
  ReviewMessage,
  ReviewThread,
} from './review.models';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function message(value: unknown): value is ReviewMessage {
  return (
    record(value) &&
    typeof value['id'] === 'string' &&
    typeof value['author'] === 'string' &&
    typeof value['text'] === 'string' &&
    value['text'].trim().length > 0 &&
    value['text'].length <= 2000
  );
}

export function validReviewDraft(value: unknown, example: ReviewExample): value is ReviewDraft {
  if (!record(value) || !record(value['files']) || !Array.isArray(value['threads'])) return false;
  const files = value['files'];
  return (
    Object.entries(files).every(
      ([path, content]) =>
        example.files.some(file => file.path === path) && typeof content === 'string',
    ) &&
    value['threads'].length <= 200 &&
    value['threads'].every((thread: unknown) => {
      if (!message(thread) || !record(thread)) return false;
      const file = example.files.find(file => file.path === thread['path']);
      return (
        file !== undefined &&
        typeof thread['line'] === 'number' &&
        Number.isInteger(thread['line']) &&
        thread['line'] >= 1 &&
        thread['line'] <= file.content.split('\n').length &&
        typeof thread['resolved'] === 'boolean' &&
        Array.isArray(thread['replies']) &&
        thread['replies'].length <= 100 &&
        thread['replies'].every(message)
      );
    })
  );
}

/** Local, account-scoped review copies; never mutates submitted solution projects. */
@Injectable({ providedIn: 'root' })
export class ReviewDemoRepository {
  private readonly cache = new Map<string, ReviewDraft>();
  private readonly revision = signal(0);
  readonly memoryOnly = signal(false);
  readonly notice = signal('');

  state(owner: string, example: ReviewExample): ReviewDraft {
    this.revision();
    return this.cache.get(this.key(owner, example.id)) ?? { files: {}, threads: example.threads };
  }

  load(owner: string, example: ReviewExample): void {
    const key = this.key(owner, example.id);
    if (!this.cache.has(key)) {
      let state: ReviewDraft = { files: {}, threads: example.threads };
      try {
        const stored = localStorage.getItem(key);
        if (stored) {
          const value: unknown = JSON.parse(stored);
          if (!validReviewDraft(value, example)) throw new Error('Invalid demo review');
          state = value;
        }
      } catch {
        this.notice.set('Не удалось восстановить демо-ревью. Показан исходный пример.');
      }
      this.cache.set(key, state);
      this.revision.update(value => value + 1);
    }
  }

  edit(owner: string, example: ReviewExample, path: string, content: string): void {
    if (!example.files.some(file => file.path === path)) return;
    const state = this.state(owner, example);
    this.write(owner, example, { ...state, files: { ...state.files, [path]: content } });
  }

  comment(
    owner: string,
    example: ReviewExample,
    author: string,
    path: string,
    line: number,
    text: string,
    replyTo: string,
  ): void {
    const file = example.files.find(file => file.path === path);
    if (!text.trim() || text.length > 2000)
      throw new ReviewInputError('text', 'Введите комментарий до 2000 символов.');
    if (!file || !Number.isInteger(line) || line < 1 || line > file.content.split('\n').length)
      throw new ReviewInputError('line', 'Выберите строку исходного файла.');
    const state = this.state(owner, example);
    const next: ReviewMessage = { id: crypto.randomUUID(), author, text: text.trim() };
    let threads: readonly ReviewThread[];
    if (replyTo) {
      const thread = state.threads.find(item => item.id === replyTo && item.path === path);
      if (!thread || thread.replies.length >= 100)
        throw new Error('Нельзя добавить ответ в эту ветку.');
      threads = state.threads.map(item =>
        item.id === replyTo ? { ...item, replies: [...item.replies, next] } : item,
      );
    } else {
      if (state.threads.length >= 200) throw new Error('В демо можно добавить до 200 обсуждений.');
      threads = [...state.threads, { ...next, path, line, resolved: false, replies: [] }];
    }
    this.write(owner, example, { ...state, threads });
  }

  resolve(owner: string, example: ReviewExample, id: string): void {
    const state = this.state(owner, example);
    this.write(owner, example, {
      ...state,
      threads: state.threads.map(thread =>
        thread.id === id ? { ...thread, resolved: !thread.resolved } : thread,
      ),
    });
  }

  save(owner: string, example: ReviewExample): void {
    this.write(owner, example, this.state(owner, example));
  }

  private write(owner: string, example: ReviewExample, state: ReviewDraft): void {
    const key = this.key(owner, example.id);
    this.cache.set(key, state);
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch {
      this.memoryOnly.set(true);
      this.notice.set(
        'Хранилище недоступно. Правки и комментарии останутся только до обновления страницы.',
      );
    }
    this.revision.update(value => value + 1);
  }

  private key(owner: string, id: string): string {
    return `prodguild.reviews-demo.v1.${encodeURIComponent(owner)}.${id}`;
  }
}
