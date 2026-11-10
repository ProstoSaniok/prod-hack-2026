import { HttpErrorResponse } from '@angular/common/http';
import { inject, Injectable, signal } from '@angular/core';
import { BackendService } from '../../api/backend.service';
import { CompetencyCatalogService } from '../../api/competency-catalog.service';
import { CaseDraft, CaseMetadataPatch, CaseRecord, CaseSkill } from './case.model';

export type CaseStatusFilter = CaseRecord['status'] | '';

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error('Invalid case response');
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid case response');
  return value;
}
function numeric(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value))
    throw new Error('Invalid case response');
  return value;
}
function parse(value: unknown): CaseRecord {
  const item = object(value);
  const status = item['status'];
  const difficulty = item['difficulty'];
  if (
    (status !== 'draft' && status !== 'archived' && status !== 'published') ||
    (difficulty !== 'performer' &&
      difficulty !== 'review_minus' &&
      difficulty !== 'review_plus' &&
      difficulty !== 'expert') ||
    typeof item['frozen'] !== 'boolean' ||
    !Array.isArray(item['competencies'])
  )
    throw new Error('Invalid case response');
  const competencies: CaseSkill[] = item['competencies'].map(value => {
    const skill = object(value);
    return {
      competency_id: string(skill['competency_id']),
      required_value: numeric(skill['required_value']),
      weight: numeric(skill['weight']),
    };
  });
  return {
    id: string(item['id']),
    name: string(item['title']),
    description: string(item['description']),
    expectedResult: string(item['expected_result']),
    createdAt: string(item['created_at']),
    version: numeric(item['version']),
    status,
    difficulty,
    frozen: item['frozen'],
    competencies,
  };
}

@Injectable({ providedIn: 'root' })
export class CasesService {
  readonly api = inject(BackendService);
  readonly competencyCatalog = inject(CompetencyCatalogService);
  readonly pageSize = 20;
  readonly cases = signal<readonly CaseRecord[]>([]);
  readonly competencies = this.competencyCatalog.items;
  readonly loading = signal(false);
  readonly allowed = signal(false);
  readonly error = signal('');
  readonly offset = signal(0);
  readonly status = signal<CaseStatusFilter>('');
  readonly hasNext = signal(false);
  private readonly pending = new Map<string, string>();
  private lastListRequest: { offset: number; status: CaseStatusFilter } | null = null;
  private generation = 0;

  async load(offset = this.offset(), status = this.status()): Promise<void> {
    const generation = ++this.generation;
    this.lastListRequest = { offset, status };
    this.loading.set(true);
    this.error.set('');
    try {
      const values = await this.api.request(
        'GET',
        `/api/v1/admin/cases?limit=${this.pageSize + 1}&offset=${offset}${status ? '&status=' + encodeURIComponent(status) : ''}`,
      );
      if (generation !== this.generation) return;
      if (!Array.isArray(values)) throw new Error('Invalid list response');
      const records = values.map(parse);
      this.cases.set(records.slice(0, this.pageSize));
      this.hasNext.set(records.length > this.pageSize);
      this.offset.set(offset);
      this.status.set(status);
      this.allowed.set(true);
    } catch (error) {
      if (generation !== this.generation) return;
      if (error instanceof HttpErrorResponse && (error.status === 401 || error.status === 403)) {
        this.cases.set([]);
        this.hasNext.set(false);
        this.offset.set(0);
        this.allowed.set(false);
      }
      this.error.set(this.message(error));
    } finally {
      if (generation === this.generation) this.loading.set(false);
    }
  }

  async retry(): Promise<void> {
    if (this.loading()) return;
    const request = this.lastListRequest;
    await this.load(request?.offset ?? this.offset(), request?.status ?? this.status());
  }

  loadCompetencies(): Promise<void> {
    return this.competencyCatalog.ensureLoaded();
  }

  async get(id: string): Promise<CaseRecord> {
    const result = parse(
      await this.api.request('GET', '/api/v1/admin/cases/' + encodeURIComponent(id)),
    );
    this.cases.update(items => items.map(item => (item.id === id ? result : item)));
    return result;
  }

  private async mutate(
    method: 'POST' | 'PATCH' | 'DELETE',
    path: string,
    body?: unknown,
  ): Promise<unknown> {
    const signature = JSON.stringify([this.api.user()?.id, method, path, body]);
    const key = this.pending.get(signature) ?? crypto.randomUUID();
    this.pending.set(signature, key);
    const response = await this.api.request(method, path, body, key);
    this.pending.delete(signature);
    return response;
  }

  async create(draft: CaseDraft): Promise<CaseRecord> {
    return parse(
      await this.mutate('POST', '/api/v1/admin/cases', {
        title: draft.name.trim(),
        description: draft.description.trim(),
        expected_result: draft.expectedResult.trim(),
        competencies: draft.competencies,
        status: 'draft',
      }),
    );
  }

  async createDraft(id: string, key: string): Promise<CaseRecord> {
    return parse(
      await this.api.request(
        'POST',
        '/api/v1/admin/cases',
        {
          id,
          title: '',
          description: '',
          expected_result: '',
          competencies: [],
          status: 'draft',
        },
        key,
      ),
    );
  }

  async saveDraft(
    id: string,
    version: number,
    draft: CaseMetadataPatch,
    key: string,
  ): Promise<CaseRecord> {
    return parse(
      await this.api.request(
        'PATCH',
        '/api/v1/admin/cases/' + encodeURIComponent(id),
        {
          expected_version: version,
          title: draft.name,
          description: draft.description,
          expected_result: draft.expectedResult,
          ...(draft.competencies === undefined ? {} : { competencies: draft.competencies }),
        },
        key,
      ),
    );
  }

  async update(record: CaseRecord, draft: CaseDraft): Promise<CaseRecord> {
    return parse(
      await this.mutate('PATCH', '/api/v1/admin/cases/' + encodeURIComponent(record.id), {
        expected_version: record.version,
        title: draft.name.trim(),
        description: draft.description.trim(),
        expected_result: draft.expectedResult.trim(),
        competencies: draft.competencies,
      }),
    );
  }

  async changeStatus(record: CaseRecord, status: CaseRecord['status']): Promise<CaseRecord> {
    return parse(
      await this.mutate('PATCH', '/api/v1/admin/cases/' + encodeURIComponent(record.id), {
        expected_version: record.version,
        status,
      }),
    );
  }

  async delete(record: CaseRecord): Promise<void> {
    await this.mutate(
      'DELETE',
      '/api/v1/admin/cases/' +
        encodeURIComponent(record.id) +
        '?expected_version=' +
        record.version,
    );
  }

  message(error: unknown): string {
    if (error instanceof HttpErrorResponse && error.status === 409) {
      const body: unknown = error.error;
      if (typeof body === 'object' && body !== null && 'code' in body) {
        if (body.code === 'STALE_VERSION')
          return 'Кейс изменён в другой вкладке. Загрузите актуальную версию и повторите изменения.';
        if (body.code === 'CASE_FROZEN')
          return 'Содержание заблокировано: по кейсу уже начали проект.';
        if (body.code === 'CASE_NOT_READY')
          return 'Для публикации подготовьте эталонное решение и зафиксируйте материалы.';
        if (body.code === 'CASE_PUBLISHED') return 'Сначала архивируйте опубликованный кейс.';
      }
      return 'Состояние кейса изменилось. Загрузите актуальную версию и повторите действие.';
    }
    if (error instanceof HttpErrorResponse && error.status === 422)
      return 'Проверьте поля кейса: уровень от 0 до 100, вес больше 0 и не больше 0,25.';
    return this.api.errorMessage(error);
  }
}
