import { inject, Injectable } from '@angular/core';
import { HttpErrorResponse } from '@angular/common/http';
import { LearningService, object, text, number } from './learning.service';

export function items<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error('Некорректный ответ сервера.');
  return value.map(parse);
}
export function nullable(value: unknown): number | null {
  return value === null ? null : number(value);
}
export interface Assignment {
  supervised_competency_ids?: readonly string[];
  kind: string;
  review_id: string | null;
  revision_id: string | null;
  id: string;
  submission_id: string;
  status: string;
  lease_expires_at: string | null;
  can_continue: boolean;
}
export interface ReviewMode {
  enabled: boolean;
  status: string;
  can_review: boolean;
  can_assess_readiness: boolean;
  tasks: readonly Assignment[];
}
export interface Comment {
  body: string;
  path?: string | null;
  line?: number | null;
  end_line?: number | null;
  side?: 'old' | 'new' | null;
}
export interface Feedback {
  strengths: string;
  problems: string;
  next_steps: string;
}
export interface Draft {
  version: number;
  score: number | null;
  verdict: string | null;
  feedback: Feedback;
  comments: Comment[];
}
export interface Review {
  assignment_id: string;
  reviewer_id: string;
  id: string;
  submission_id: string;
  status: string;
  current_revision: Omit<Draft, 'version'> & {
    id: string;
    number: number;
    draft_version: number | null;
  };
}
export interface ReviewMeta {
  id: string;
  submission_id: string;
  status: string;
}
export interface Workspace {
  assignment: Assignment;
  draft: Draft;
  case: { title: string; description: string; expected_result: string };
}
export interface Contribution {
  competency_id: string;
  contribution: number;
  displayed_value_before: number;
  displayed_value_after: number;
}
function assignment(value: unknown): Assignment {
  const v = object(value);
  return {
    id: text(v['id']),
    kind: text(v['kind']),
    ...(v['supervised_competency_ids'] === undefined
      ? {}
      : { supervised_competency_ids: items(v['supervised_competency_ids'], text) }),
    review_id: v['review_id'] === null ? null : text(v['review_id']),
    revision_id: v['revision_id'] === null ? null : text(v['revision_id']),
    submission_id: text(v['submission_id']),
    status: text(v['status']),
    lease_expires_at:
      v['lease_expires_at'] === null || v['lease_expires_at'] === undefined
        ? null
        : text(v['lease_expires_at']),
    can_continue: v['can_continue'] !== false,
  };
}
function feedback(value: unknown): Feedback {
  const v = object(value);
  return {
    strengths: text(v['strengths']),
    problems: text(v['problems']),
    next_steps: text(v['next_steps']),
  };
}
function comment(value: unknown): Comment {
  const v = object(value);
  const side = v['side'];
  if (side !== null && side !== undefined && side !== 'old' && side !== 'new')
    throw new Error('Некорректная сторона комментария.');
  return {
    body: text(v['body']),
    path: v['path'] === null || v['path'] === undefined ? null : text(v['path']),
    line: v['line'] === null || v['line'] === undefined ? null : number(v['line']),
    ...(v['end_line'] === null || v['end_line'] === undefined
      ? {}
      : { end_line: number(v['end_line']) }),
    side,
  };
}
function draft(value: unknown): Draft {
  const v = object(value);
  return {
    version: number(v['version']),
    score: nullable(v['score']),
    verdict: v['verdict'] === null ? null : text(v['verdict']),
    feedback: feedback(v['feedback']),
    comments: items(v['comments'], comment),
  };
}
function meta(value: unknown): ReviewMeta {
  const v = object(value);
  return { id: text(v['id']), submission_id: text(v['submission_id']), status: text(v['status']) };
}
function review(value: unknown): Review {
  const v = object(value),
    r = object(v['current_revision']);
  return {
    ...meta(v),
    assignment_id: text(v['assignment_id']),
    reviewer_id: text(v['reviewer_id']),
    current_revision: {
      id: text(r['id']),
      number: number(r['number']),
      draft_version: nullable(r['draft_version']),
      score: nullable(r['score']),
      verdict: r['verdict'] === null ? null : text(r['verdict']),
      feedback: feedback(r['feedback']),
      comments: items(r['comments'], comment),
    },
  };
}
export interface QualityAssessment {
  id: string;
  revision_id: string;
  decision: string;
  score: number;
  feedback?: string;
}
export interface QualityAssessmentRecord extends QualityAssessment {
  assignment_id: string;
  review_id: string;
  assessor_id: string;
  created_at: string;
}
export interface Probation {
  id: string;
  competency_id: string;
  grade: string;
  confirmed_reviews: number;
  required_successes: number;
  remaining: number;
}
function assessment(value: unknown): QualityAssessment {
  const v = object(value);
  return {
    id: text(v['id']),
    revision_id: text(v['revision_id']),
    decision: text(v['decision']),
    score: number(v['score']),
    ...(typeof v['feedback'] === 'string' ? { feedback: v['feedback'] } : {}),
  };
}
function assessmentRecord(value: unknown): QualityAssessmentRecord {
  const v = object(value);
  return {
    ...assessment(v),
    assignment_id: text(v['assignment_id']),
    review_id: text(v['review_id']),
    assessor_id: text(v['assessor_id']),
    created_at: text(v['created_at']),
  };
}
@Injectable({ providedIn: 'root' })
export class ReviewService {
  readonly learning = inject(LearningService);
  async mode(): Promise<ReviewMode> {
    return this.parseMode(await this.learning.api.request('GET', '/api/v1/me/review-mode'));
  }
  private parseMode(value: unknown): ReviewMode {
    const v = object(value);
    if (typeof v['enabled'] !== 'boolean') throw new Error('Некорректный режим ревью.');
    return {
      enabled: v['enabled'],
      status: text(v['status']),
      can_review: v['can_review'] === true,
      can_assess_readiness: v['can_assess_readiness'] === true,
      tasks: v['tasks'] === undefined ? [] : items(v['tasks'], assignment),
    };
  }
  async setMode(enabled: boolean): Promise<ReviewMode> {
    return this.parseMode(await this.learning.mutate('PUT', '/api/v1/me/review-mode', { enabled }));
  }
  async claim(): Promise<Assignment | null> {
    const value = await this.learning.mutate('POST', '/api/v1/me/review-assignments');
    return value === null ? null : assignment(value);
  }
  async cancel(id: string): Promise<void> {
    await this.learning.mutate('DELETE', `/api/v1/review-assignments/${encodeURIComponent(id)}`);
  }
  async assignments(offset = 0): Promise<Assignment[]> {
    return items(
      await this.learning.api.request(
        'GET',
        `/api/v1/me/review-assignments?limit=20&offset=${offset}`,
      ),
      assignment,
    );
  }
  async assignment(id: string): Promise<Assignment> {
    return assignment(
      await this.learning.api.request(
        'GET',
        `/api/v1/review-assignments/${encodeURIComponent(id)}`,
      ),
    );
  }
  async workspace(id: string): Promise<Workspace> {
    const v = object(
        await this.learning.api.request(
          'GET',
          `/api/v1/review-assignments/${encodeURIComponent(id)}/workspace`,
        ),
      ),
      c = object(v['case']);
    return {
      assignment: assignment(v['assignment']),
      draft: draft(v['draft']),
      case: {
        title: text(c['title']),
        description: text(c['description']),
        expected_result: text(c['expected_result']),
      },
    };
  }
  async draft(id: string): Promise<Draft> {
    return draft(
      await this.learning.api.request(
        'GET',
        `/api/v1/review-assignments/${encodeURIComponent(id)}/draft`,
      ),
    );
  }
  async save(id: string, content: Omit<Draft, 'version'>, version: number): Promise<Draft> {
    return draft(
      await this.learning.mutate(
        'PUT',
        `/api/v1/review-assignments/${encodeURIComponent(id)}/draft`,
        { ...content, expected_version: version },
      ),
    );
  }
  async complete(id: string, version: number): Promise<ReviewMeta> {
    return meta(
      await this.learning.mutate(
        'POST',
        `/api/v1/review-assignments/${encodeURIComponent(id)}/reviews`,
        { expected_draft_version: version },
      ),
    );
  }
  async reviews(offset = 0): Promise<{ items: ReviewMeta[]; next_offset: number | null }> {
    const v = object(
      await this.learning.api.request('GET', `/api/v1/me/reviews?limit=20&offset=${offset}`),
    );
    return { items: items(v['items'], meta), next_offset: nullable(v['next_offset']) };
  }
  async review(id: string): Promise<Review> {
    return review(
      await this.learning.api.request('GET', `/api/v1/reviews/${encodeURIComponent(id)}`),
    );
  }
  async feedback(id: string): Promise<Review[]> {
    const v = object(
      await this.learning.api.request(
        'GET',
        `/api/v1/submissions/${encodeURIComponent(id)}/feedback`,
      ),
    );
    return items(v['reviews'], review).filter(item => item.status === 'published');
  }
  async progression(id: string): Promise<Contribution[]> {
    const v = object(
      await this.learning.api.request(
        'GET',
        `/api/v1/submissions/${encodeURIComponent(id)}/progression`,
      ),
    );
    return items(v['contributions'], item => {
      const f = object(item);
      return {
        competency_id: text(f['competency_id']),
        contribution: number(f['contribution']),
        displayed_value_before: number(f['displayed_value_before']),
        displayed_value_after: number(f['displayed_value_after']),
      };
    });
  }
  async source(id: string): Promise<Review & { workspace: Workspace }> {
    const v = object(
      await this.learning.api.request(
        'GET',
        `/api/v1/review-assignments/${encodeURIComponent(id)}/source-review`,
      ),
    );
    const w = object(v['workspace']),
      c = object(w['case']);
    return {
      ...review(v),
      workspace: {
        assignment: assignment(w['assignment']),
        draft: draft(w['draft']),
        case: {
          title: text(c['title']),
          description: text(c['description']),
          expected_result: text(c['expected_result']),
        },
      },
    };
  }
  async assess(
    reviewId: string,
    body: {
      assignment_id: string;
      revision_id: string;
      decision: string;
      score: number;
      feedback: string;
    },
  ): Promise<QualityAssessment> {
    return assessment(
      await this.learning.mutate(
        'POST',
        `/api/v1/reviews/${encodeURIComponent(reviewId)}/quality-assessments`,
        body,
      ),
    );
  }
  async assessments(
    id: string,
    offset = 0,
  ): Promise<{ items: QualityAssessment[]; next_offset: number | null }> {
    const v = object(
      await this.learning.api.request(
        'GET',
        `/api/v1/reviews/${encodeURIComponent(id)}/quality-assessments?limit=20&offset=${offset}`,
      ),
    );
    return { items: items(v['items'], assessment), next_offset: nullable(v['next_offset']) };
  }
  async assessment(id: string, assessmentId: string): Promise<QualityAssessment> {
    return assessment(
      await this.learning.api.request(
        'GET',
        `/api/v1/reviews/${encodeURIComponent(id)}/quality-assessments/${encodeURIComponent(assessmentId)}`,
      ),
    );
  }
  async assessmentRecords(
    id: string,
    offset = 0,
  ): Promise<{ items: QualityAssessmentRecord[]; next_offset: number | null }> {
    const v = object(
      await this.learning.api.request(
        'GET',
        `/api/v1/reviews/${encodeURIComponent(id)}/quality-assessments?limit=20&offset=${offset}`,
      ),
    );
    return { items: items(v['items'], assessmentRecord), next_offset: nullable(v['next_offset']) };
  }
  async assessmentRecord(
    id: string,
    assessmentId: string,
  ): Promise<QualityAssessmentRecord & { feedback: string }> {
    const v = object(
      await this.learning.api.request(
        'GET',
        `/api/v1/reviews/${encodeURIComponent(id)}/quality-assessments/${encodeURIComponent(assessmentId)}`,
      ),
    );
    return { ...assessmentRecord(v), feedback: text(v['feedback']) };
  }
  async revision(id: string, revisionId: string): Promise<Review> {
    return review(
      await this.learning.api.request(
        'GET',
        `/api/v1/reviews/${encodeURIComponent(id)}/revisions/${encodeURIComponent(revisionId)}`,
      ),
    );
  }
  async revise(id: string, revisionId: string, version: number): Promise<{ id: string }> {
    const value = object(
      await this.learning.mutate('POST', `/api/v1/reviews/${encodeURIComponent(id)}/revisions`, {
        expected_revision_id: revisionId,
        expected_draft_version: version,
      }),
    );
    return { id: text(value['review_id']) };
  }
  async probation(offset = 0): Promise<{ items: Probation[]; next_offset: number | null }> {
    const v = object(
      await this.learning.api.request('GET', `/api/v1/me/probation?limit=20&offset=${offset}`),
    );
    return {
      items: items(v['items'], item => {
        const p = object(item);
        return {
          id: text(p['id']),
          competency_id: text(p['competency_id']),
          grade: text(p['grade']),
          confirmed_reviews: number(p['confirmed_reviews']),
          required_successes: number(p['required_successes']),
          remaining: number(p['remaining']),
        };
      }),
      next_offset: nullable(v['next_offset']),
    };
  }
  message(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const value: unknown = error.error;
      const code = value && typeof value === 'object' && 'code' in value ? value.code : '';
      if (code === 'STALE_REVISION')
        return 'Редакция ревью изменилась. Загрузите актуальную редакцию перед повторной отправкой.';
      if (code === 'STALE_VERSION')
        return 'Версия черновика изменилась. Загрузите серверный черновик для сравнения: ваш текст останется на странице.';
      if (code === 'ASSIGNMENT_CLOSED') return 'Назначение завершено или возвращено в очередь.';
      if (code === 'REVIEW_MODE_DISABLED') return 'Сначала включите режим ревью.';
      if (code === 'CAPACITY_LIMIT') return 'У вас уже есть активное назначение. Продолжите его.';
      if (code === 'SUPERVISION_REQUIRED')
        return 'Нужно экспертное подтверждение. Проверьте статус ревью и назначение экспертной проверки.';
      if (error.status === 403)
        return 'Для ревью нужны подтверждённые права по всем навыкам кейса.';
      if (error.status === 422)
        return 'Проверьте оценку, вердикт, обратную связь и координаты комментариев.';
    }
    return this.learning.message(error);
  }
}
