import { inject, Injectable } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { BackendService } from './backend.service';
import { CaseDifficulty } from './case-presentation';

export interface PublishedCase {
  id: string;
  title: string;
  description: string;
  expected_result: string;
  difficulty: string;
  content_version: number;
  competencies: readonly { competency_id: string; required_value: number; weight: number }[];
}
export interface CatalogFilters {
  q: string;
  difficulty: CaseDifficulty | '';
  competencyIds: readonly string[];
}
export interface Project {
  id: string;
  case_id: string;
  case_version: number;
  status: string;
  head_commit_sha: string;
  attachments_version?: number;
  submission_id?: string;
}
export interface Submission {
  id: string;
  project_id: string;
  case_id: string;
  case_version: number;
  status: string;
  submitted_at: string;
  commit_sha: string;
  base_commit_sha: string;
  summary?: string;
  priority: SubmissionPriority | null;
}
export interface PriorityRequest {
  id: string;
  submission_id: string;
  cost: number;
  created_at: string;
  status: 'reserved' | 'settled' | 'cancelled' | 'refunded';
}
export interface SubmissionPriority {
  cost: number;
  can_purchase: boolean;
  can_cancel: boolean;
  current_request: PriorityRequest | null;
}
export function parsePriority(value: unknown): PriorityRequest {
  const v = object(value);
  const status = v['status'];
  if (
    status !== 'reserved' &&
    status !== 'settled' &&
    status !== 'cancelled' &&
    status !== 'refunded'
  )
    throw new Error('Некорректный статус приоритета.');
  return {
    id: text(v['id']),
    submission_id: text(v['submission_id']),
    cost: number(v['cost']),
    created_at: text(v['created_at']),
    status,
  };
}
function parseSubmissionPriority(value: unknown): SubmissionPriority | null {
  if (value === undefined || value === null) return null;
  const v = object(value);
  if (typeof v['can_purchase'] !== 'boolean' || typeof v['can_cancel'] !== 'boolean')
    throw new Error('Некорректный статус приоритета.');
  return {
    cost: number(v['cost']),
    can_purchase: v['can_purchase'],
    can_cancel: v['can_cancel'],
    current_request: v['current_request'] === null ? null : parsePriority(v['current_request']),
  };
}
export interface FilePage {
  entries: readonly { path: string; sha: string }[];
  next_offset: number | null;
}
export interface DiffPage {
  entries: readonly {
    comment_path: string;
    status: string;
    additions: number;
    deletions: number;
  }[];
  next_offset: number | null;
}
export interface LinePage {
  lines: readonly {
    kind: string;
    old_line: number | null;
    new_line: number | null;
    text: string;
    text_truncated: boolean;
  }[];
  next_offset: number | null;
}
function nullableNumber(value: unknown): number | null {
  return value === null ? null : number(value);
}
function parseSubmission(value: unknown): Submission {
  const v = object(value);
  return {
    id: text(v['id']),
    project_id: text(v['project_id']),
    case_id: text(v['case_id']),
    case_version: number(v['case_version']),
    status: text(v['status']),
    submitted_at: text(v['submitted_at']),
    commit_sha: text(v['commit_sha']),
    base_commit_sha: text(v['base_commit_sha']),
    priority: parseSubmissionPriority(v['priority']),
    ...(typeof v['summary'] === 'string' ? { summary: v['summary'] } : {}),
  };
}
export interface Reference {
  version: number;
  head_commit_sha: string;
  ready: boolean;
}
export interface RemoteFile {
  path: string;
  sha: string;
  commit_sha: string;
  size?: number;
  content?: string;
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Некорректный ответ сервера.');
  return value as Record<string, unknown>;
}
export function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Некорректный ответ сервера.');
  return value;
}
export function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new Error('Некорректный ответ сервера.');
  return value;
}
function list<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error('Некорректный ответ сервера.');
  return value.map(parse);
}
function parseCase(value: unknown): PublishedCase {
  const v = object(value);
  return {
    id: text(v['id']),
    title: text(v['title']),
    description: text(v['description']),
    expected_result: text(v['expected_result']),
    difficulty: text(v['difficulty']),
    content_version: number(v['content_version']),
    competencies: list(v['competencies'], value => {
      const s = object(value);
      return {
        competency_id: text(s['competency_id']),
        required_value: number(s['required_value']),
        weight: number(s['weight']),
      };
    }),
  };
}
function parseProject(value: unknown): Project {
  const v = object(value);
  return {
    id: text(v['id']),
    case_id: text(v['case_id']),
    case_version: number(v['case_version']),
    status: text(v['status']),
    head_commit_sha: text(v['head_commit_sha']),
    attachments_version:
      v['attachments_version'] === undefined ? 0 : number(v['attachments_version']),
    ...(typeof v['submission_id'] === 'string' ? { submission_id: v['submission_id'] } : {}),
  };
}
function parseFile(value: unknown): RemoteFile {
  const v = object(value);
  return {
    path: text(v['path']),
    sha: text(v['sha']),
    commit_sha: text(v['commit_sha']),
    ...(typeof v['size'] === 'number' ? { size: number(v['size']) } : {}),
    ...(typeof v['content'] === 'string' ? { content: v['content'] } : {}),
  };
}
@Injectable({ providedIn: 'root' })
export class LearningService {
  readonly api = inject(BackendService);
  private readonly pending = new Map<string, string>();
  // Keep an uncertain attempt across SPA navigation and reauthentication, scoped
  // to the original participant/project. Reload is protected by beforeunload.
  private readonly submissionAttempts = new Map<
    string,
    { project: Project; summary: string; key: string; uncertain: boolean }
  >();
  async mutate(
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
    path: string,
    body?: unknown,
  ): Promise<unknown> {
    const signature = JSON.stringify([this.api.user()?.id, method, path, body]);
    const key = this.pending.get(signature) ?? crypto.randomUUID();
    this.pending.set(signature, key);
    const result = await this.api.request(method, path, body, key, 100000);
    this.pending.delete(signature);
    return result;
  }
  async catalogue(offset = 0, filters?: CatalogFilters): Promise<PublishedCase[]> {
    const params = new URLSearchParams({ limit: '21', offset: String(offset) });
    if (filters?.q) params.set('q', filters.q);
    if (filters?.difficulty) params.set('difficulty', filters.difficulty);
    if (filters?.competencyIds.length)
      params.set('competency_ids', filters.competencyIds.join(','));
    return list(await this.api.request('GET', `/api/v1/cases?${params}`), parseCase);
  }
  async case(id: string): Promise<PublishedCase> {
    return parseCase(await this.api.request('GET', `/api/v1/cases/${encodeURIComponent(id)}`));
  }
  async projects(offset = 0): Promise<Project[]> {
    return list(
      await this.api.request('GET', `/api/v1/me/projects?limit=20&offset=${offset}`),
      parseProject,
    );
  }
  async project(id: string): Promise<Project> {
    return parseProject(
      await this.api.request('GET', `/api/v1/projects/${encodeURIComponent(id)}`),
    );
  }
  async start(caseId: string): Promise<Project> {
    return parseProject(await this.mutate('POST', '/api/v1/me/projects', { case_id: caseId }));
  }
  async submit(project: Project, summary: string): Promise<Submission> {
    const owner = this.api.user()?.id;
    if (!owner) throw new Error('Войдите снова, чтобы продолжить отправку.');
    const signature = JSON.stringify([owner, project.id]);
    const attempt = this.submissionAttempts.get(signature) ?? {
      project: { ...project },
      summary,
      key: crypto.randomUUID(),
      uncertain: false,
    };
    this.submissionAttempts.set(signature, attempt);
    const path = `/api/v1/projects/${encodeURIComponent(project.id)}/submissions`;
    const body = {
      expected_commit_sha: attempt.project.head_commit_sha,
      ...(attempt.summary.trim() ? { summary: attempt.summary.trim() } : {}),
    };
    try {
      const result = parseSubmission(
        await this.api.request('POST', path, body, attempt.key, 100000),
      );
      this.submissionAttempts.delete(signature);
      return result;
    } catch (error) {
      // A failed authentication retry cannot disprove an earlier committed write.
      if (
        this.submissionNeedsChanges(error) ||
        (!attempt.uncertain && this.submissionRejected(error))
      )
        this.submissionAttempts.delete(signature);
      else attempt.uncertain = true;
      if (
        error instanceof HttpErrorResponse &&
        error.status === 409 &&
        error.error?.code === 'PROJECT_SUBMITTED'
      ) {
        const current = await this.project(project.id);
        if (current.submission_id) {
          const result = await this.submission(current.submission_id);
          this.submissionAttempts.delete(signature);
          return result;
        }
      }
      throw error;
    }
  }
  pendingSubmission(projectId: string): { project: Project; summary: string } | undefined {
    return this.submissionAttempts.get(JSON.stringify([this.api.user()?.id, projectId]));
  }
  submissionNeedsChanges(error: unknown): boolean {
    return (
      error instanceof HttpErrorResponse &&
      error.status === 409 &&
      ['NO_CHANGES', 'COMMIT_CHANGED'].includes(error.error?.code)
    );
  }
  submissionRejected(error: unknown): boolean {
    return (
      error instanceof HttpErrorResponse &&
      ([401, 403, 422].includes(error.status) || this.submissionNeedsChanges(error))
    );
  }
  async submissions(offset = 0): Promise<Submission[]> {
    return list(
      await this.api.request('GET', `/api/v1/me/submissions?limit=20&offset=${offset}`),
      parseSubmission,
    );
  }
  async submission(id: string): Promise<Submission> {
    return parseSubmission(
      await this.api.request('GET', `/api/v1/submissions/${encodeURIComponent(id)}`),
    );
  }
  async submissionFiles(
    id: string,
    side: 'base' | 'head',
    offset = 0,
    kind: 'submissions' | 'review-assignments' = 'submissions',
  ): Promise<FilePage> {
    const v = object(
      await this.api.request(
        'GET',
        `/api/v1/${kind}/${encodeURIComponent(id)}/files?side=${side}&limit=20&offset=${offset}`,
      ),
    );
    return {
      entries: list(v['entries'], item => {
        const f = object(item);
        return { path: text(f['path']), sha: text(f['sha']) };
      }),
      next_offset: nullableNumber(v['next_offset']),
    };
  }
  async submissionFile(
    id: string,
    path: string,
    side: 'base' | 'head',
    kind: 'submissions' | 'review-assignments' = 'submissions',
  ): Promise<RemoteFile> {
    return parseFile(
      await this.api.request(
        'GET',
        `/api/v1/${kind}/${encodeURIComponent(id)}/files/content?side=${side}&path=${encodeURIComponent(path)}`,
      ),
    );
  }
  async submissionDiff(
    id: string,
    offset = 0,
    kind: 'submissions' | 'review-assignments' = 'submissions',
  ): Promise<DiffPage> {
    const v = object(
      await this.api.request(
        'GET',
        `/api/v1/${kind}/${encodeURIComponent(id)}/diff?limit=20&offset=${offset}`,
      ),
    );
    return {
      entries: list(v['entries'], item => {
        const f = object(item);
        return {
          comment_path: text(f['comment_path']),
          status: text(f['status']),
          additions: number(f['additions']),
          deletions: number(f['deletions']),
        };
      }),
      next_offset: nullableNumber(v['next_offset']),
    };
  }
  async submissionLines(
    id: string,
    path: string,
    offset = 0,
    kind: 'submissions' | 'review-assignments' = 'submissions',
  ): Promise<LinePage> {
    const v = object(
      await this.api.request(
        'GET',
        `/api/v1/${kind}/${encodeURIComponent(id)}/diff/lines?limit=100&offset=${offset}&path=${encodeURIComponent(path)}`,
      ),
    );
    return {
      lines: list(v['lines'], item => {
        const f = object(item);
        if (typeof f['text_truncated'] !== 'boolean')
          throw new Error('Некорректный ответ сервера.');
        return {
          kind: text(f['kind']),
          old_line: nullableNumber(f['old_line']),
          new_line: nullableNumber(f['new_line']),
          text: text(f['text']),
          text_truncated: f['text_truncated'],
        };
      }),
      next_offset: nullableNumber(v['next_offset']),
    };
  }
  async submissionSnapshot(
    id: string,
    kind: 'submissions' | 'review-assignments' = 'submissions',
  ): Promise<unknown> {
    return this.api.request('GET', `/api/v1/${kind}/${encodeURIComponent(id)}/snapshot`);
  }
  async reference(id: string): Promise<Reference | null> {
    try {
      const v = object(
        await this.api.request(
          'GET',
          `/api/v1/admin/cases/${encodeURIComponent(id)}/reference-solution`,
        ),
      );
      if (typeof v['ready'] !== 'boolean') throw new Error('Некорректный ответ сервера.');
      return {
        version: number(v['version']),
        head_commit_sha: text(v['head_commit_sha']),
        ready: v['ready'],
      };
    } catch (e) {
      if (e instanceof HttpErrorResponse && e.status === 404) return null;
      throw e;
    }
  }
  async files(prefix: string): Promise<RemoteFile[]> {
    return list(await this.api.request('GET', prefix + '/files'), parseFile);
  }
  async file(prefix: string, path: string): Promise<RemoteFile> {
    return parseFile(
      await this.api.request('GET', prefix + '/files/content?path=' + encodeURIComponent(path)),
    );
  }
  message(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 503)
        return 'Сервис временно недоступен. Повторите операцию с теми же данными.';
      const code = error.error && typeof error.error === 'object' ? error.error.code : '';
      if (code === 'NO_CHANGES')
        return 'Решение не отличается от исходных материалов. Измените и сохраните файл.';
      if (code === 'COMMIT_CHANGED')
        return 'Файлы изменились в другой вкладке. Обновите проект и проверьте содержимое перед отправкой.';
      if (code === 'PROJECT_SUBMITTED')
        return 'Проект уже отправлен. Обновите страницу, чтобы открыть результат.';
      if (error.status === 409)
        return 'Версия или состояние изменились. Обновите данные перед редактированием; незавершённую операцию повторите с теми же данными.';
      if (error.status === 403)
        return 'Для этой операции нужны соответствующие права участника или организатора.';
      if (error.status === 422)
        return 'Проверьте путь файла и содержимое: поддерживается UTF-8 текст до 1 МиБ.';
    }
    return this.api.errorMessage(error);
  }
}
