import { HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { BackendService, ProfileSkill } from './backend.service';
import { number, object, parsePriority, PriorityRequest, text } from './learning.service';
import { WorkflowMutationsService } from './workflow-mutations.service';

export interface WorkflowPage<T> {
  items: T[];
  next_offset: number | null;
}
export interface ReviewerApplication {
  id: string;
  user_id: string;
  competency_id: string;
  competency_name: string;
  status: 'pending' | 'approved' | 'needs_development' | 'cancelled';
  version: number;
  created_at: string;
}
export interface ReadinessAssessment {
  id: string;
  decision: 'approved' | 'needs_development';
  created_at: string;
  feedback?: string;
}
export interface EvidenceReview {
  review_id: string;
  case_title: string;
  published_at: string;
  quality_score: string;
}
export interface ApplicationDetail extends ReviewerApplication {
  current_user_competency_version: number;
  can_assess: boolean;
  assessments: ReadinessAssessment[];
  evidence_summary: {
    value: string;
    successful_cases: { case_id: string; title: string; score: string }[];
    probations: {
      id: string;
      grade: string;
      confirmed_reviews: number;
      completed_at: string | null;
    }[];
    reviews: EvidenceReview[];
  };
}
export interface PublishedEvidence {
  review_id: string;
  case: { title: string; description: string; expected_result: string };
  published_at: string;
  score: string;
  verdict: string;
  feedback: { strengths: string; problems: string; next_steps: string };
  comments: {
    body: string;
    path: string | null;
    line: number | null;
    end_line?: number | null;
    side: string | null;
  }[];
  quality: { score: string; confirmed_at: string };
}
function array<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error('Некорректный ответ сервера.');
  return value.map(parse);
}
function application(value: unknown): ReviewerApplication {
  const v = object(value);
  const status = v['status'];
  if (
    status !== 'pending' &&
    status !== 'approved' &&
    status !== 'needs_development' &&
    status !== 'cancelled'
  )
    throw new Error('Неизвестный статус заявки.');
  return {
    id: text(v['id']),
    user_id: text(v['user_id']),
    competency_id: text(v['competency_id']),
    competency_name: text(v['competency_name']),
    status,
    version: number(v['version']),
    created_at: text(v['created_at']),
  };
}
function assessment(value: unknown): ReadinessAssessment {
  const v = object(value);
  const decision = v['decision'];
  if (decision !== 'approved' && decision !== 'needs_development')
    throw new Error('Неизвестное решение эксперта.');
  return {
    id: text(v['id']),
    decision,
    created_at: text(v['created_at']),
    ...(typeof v['feedback'] === 'string' ? { feedback: v['feedback'] } : {}),
  };
}

@Injectable({ providedIn: 'root' })
export class DevelopmentService {
  readonly api = inject(BackendService);
  readonly mutations = inject(WorkflowMutationsService);
  readonly applicationStatus: Readonly<Record<ReviewerApplication['status'], string>> = {
    pending: 'Ожидает эксперта',
    approved: 'Подтверждена',
    needs_development: 'Нужно развитие',
    cancelled: 'Отменена',
  };
  readonly priorityStatus: Readonly<Record<PriorityRequest['status'], string>> = {
    reserved: 'Средства зарезервированы',
    settled: 'Приоритет оплачен',
    cancelled: 'Приоритет отменён',
    refunded: 'Оплата возвращена',
  };
  applicationAction(skillId: string): string {
    return `application:${skillId}`;
  }
  assessmentAction(id: string): string {
    return `assessment:${id}`;
  }
  priorityAction(id: string): string {
    return `priority:${id}`;
  }

  async applications(expert: boolean, offset = 0): Promise<WorkflowPage<ReviewerApplication>> {
    const v = object(
      await this.api.request(
        'GET',
        `/api/v1/${expert ? '' : 'me/'}reviewer-applications?limit=20&offset=${offset}`,
      ),
    );
    return {
      items: array(v['items'], application),
      next_offset: v['next_offset'] === null ? null : number(v['next_offset']),
    };
  }
  async application(id: string): Promise<ApplicationDetail> {
    const v = object(
      await this.api.request('GET', `/api/v1/reviewer-applications/${encodeURIComponent(id)}`),
    );
    const summary = object(v['evidence_summary']);
    if (typeof v['can_assess'] !== 'boolean') throw new Error('Права проверки заявки недоступны.');
    return {
      ...application(v),
      current_user_competency_version: number(v['current_user_competency_version']),
      can_assess: v['can_assess'],
      assessments: array(v['assessments'], assessment),
      evidence_summary: {
        value: text(summary['value']),
        successful_cases: array(summary['successful_cases'], item => {
          const c = object(item);
          return { case_id: text(c['case_id']), title: text(c['title']), score: text(c['score']) };
        }),
        probations: array(summary['probations'], item => {
          const p = object(item);
          return {
            id: text(p['id']),
            grade: text(p['grade']),
            confirmed_reviews: number(p['confirmed_reviews']),
            completed_at: p['completed_at'] === null ? null : text(p['completed_at']),
          };
        }),
        reviews: array(summary['reviews'], item => {
          const r = object(item);
          return {
            review_id: text(r['review_id']),
            case_title: text(r['case_title']),
            published_at: text(r['published_at']),
            quality_score: text(r['quality_score']),
          };
        }),
      },
    };
  }
  async apply(skill: ProfileSkill): Promise<string> {
    const result = object(
      await this.mutations.mutate(
        this.applicationAction(skill.competency_id),
        'POST',
        '/api/v1/reviewer-applications',
        {
          competency_id: skill.competency_id,
          requested_grade: 'expert',
          expected_user_competency_version: skill.version,
        },
      ),
    );
    return text(result['id']);
  }
  async retryApplication(skillId: string): Promise<string> {
    return text(object(await this.mutations.retry(this.applicationAction(skillId)))['id']);
  }
  async assess(
    detail: ApplicationDetail,
    decision: 'approved' | 'needs_development',
    feedback: string,
  ): Promise<void> {
    await this.mutations.mutate(
      this.assessmentAction(detail.id),
      'POST',
      `/api/v1/reviewer-applications/${encodeURIComponent(detail.id)}/assessments`,
      {
        expected_application_status: 'pending',
        expected_application_version: detail.version,
        expected_user_competency_version: detail.current_user_competency_version,
        decision,
        feedback: feedback.trim(),
      },
    );
  }
  async assessment(id: string, assessmentId: string): Promise<ReadinessAssessment> {
    return assessment(
      await this.api.request(
        'GET',
        `/api/v1/reviewer-applications/${encodeURIComponent(id)}/assessments/${encodeURIComponent(assessmentId)}`,
      ),
    );
  }
  async evidence(id: string, reviewId: string): Promise<PublishedEvidence> {
    const v = object(
      await this.api.request(
        'GET',
        `/api/v1/reviewer-applications/${encodeURIComponent(id)}/evidence/reviews/${encodeURIComponent(reviewId)}`,
      ),
    );
    const study = object(v['case']);
    const feedback = object(v['feedback']);
    const quality = object(v['quality']);
    return {
      review_id: text(v['review_id']),
      case: {
        title: text(study['title']),
        description: text(study['description']),
        expected_result: text(study['expected_result']),
      },
      published_at: text(v['published_at']),
      score: text(v['score']),
      verdict: text(v['verdict']),
      feedback: {
        strengths: text(feedback['strengths']),
        problems: text(feedback['problems']),
        next_steps: text(feedback['next_steps']),
      },
      comments: array(v['comments'], item => {
        const c = object(item);
        return {
          body: text(c['body']),
          path: c['path'] === null ? null : text(c['path']),
          line: c['line'] === null ? null : number(c['line']),
          end_line:
            c['end_line'] === null || c['end_line'] === undefined ? null : number(c['end_line']),
          side: c['side'] === null ? null : text(c['side']),
        };
      }),
      quality: { score: text(quality['score']), confirmed_at: text(quality['confirmed_at']) },
    };
  }
  async priorities(offset = 0): Promise<WorkflowPage<PriorityRequest>> {
    const response = await this.api.request(
      'GET',
      `/api/v1/me/priority-requests?limit=20&offset=${offset}`,
    );
    const items = array(response, parsePriority);
    return { items, next_offset: items.length === 20 ? offset + 20 : null };
  }
  async purchase(submissionId: string): Promise<void> {
    await this.mutations.mutate(
      this.priorityAction(submissionId),
      'POST',
      `/api/v1/submissions/${encodeURIComponent(submissionId)}/priority-requests`,
    );
  }
  async cancelPriority(submissionId: string, requestId: string): Promise<void> {
    await this.mutations.mutate(
      this.priorityAction(submissionId),
      'DELETE',
      `/api/v1/priority-requests/${encodeURIComponent(requestId)}`,
    );
  }
  message(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const code: unknown =
        error.error && typeof error.error === 'object' ? error.error.code : null;
      if (code === 'INSUFFICIENT_FUNDS')
        return 'Недостаточно средств. Пополнения за решения и ревью появятся в балансе.';
      if (code === 'GRADE_REQUIREMENTS_NOT_MET')
        return 'Условия подачи заявки пока не выполнены. Обновите профиль.';
      if (code === 'STALE_VERSION')
        return 'Данные изменились. Обновите заявку, проверьте новые данные и отправьте решение заново. Ваш комментарий сохранён.';
      if (code === 'APPLICATION_COMPLETED')
        return 'Другой эксперт уже принял решение. Обновите заявку. Ваш комментарий сохранён.';
      if (
        code === 'JOB_ALREADY_ASSIGNED' ||
        code === 'PRIORITY_NOT_CANCELLABLE' ||
        code === 'PRIORITY_ALREADY_ACTIVE'
      )
        return 'Состояние очереди изменилось. Обновите статус приоритета.';
      if (error.status === 422)
        return 'Проверьте решение и комментарий: нужен непустой текст до 6000 символов.';
      if (error.status === 404 || error.status === 403)
        return 'Данные недоступны или права изменились. Обновите страницу.';
      if (error.status === 409) return 'Данные изменились. Обновите их перед повторной отправкой.';
    }
    return this.api.errorMessage(error);
  }
}
