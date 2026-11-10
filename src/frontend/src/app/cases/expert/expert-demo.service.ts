import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { BackendService } from '../../api/backend.service';
import { reviewExamples } from '../reviews/review-examples';
import { ReviewExample, ReviewThread } from '../reviews/review.models';
import { validReviewDraft } from '../reviews/review-demo.repository';

export type SolutionVerdict = 'approved' | 'changes_requested';
export type QualityVerdict = 'confirmed' | 'needs_revision';
export interface OriginalReview {
  readonly reviewerEmail: string;
  readonly reviewerName: string;
  readonly verdict: SolutionVerdict;
  readonly feedback: string;
}
export interface ExpertWork {
  readonly id: string;
  readonly authorEmail: string;
  readonly example: ReviewExample;
  readonly review: OriginalReview | null;
}
export interface ExpertDraft {
  readonly verdict: SolutionVerdict;
  readonly feedback: string;
  readonly score: number | null;
  readonly qualityVerdict: QualityVerdict;
  readonly strengths: string;
  readonly issues: string;
  readonly recommendations: string;
}
export interface ExpertResult {
  readonly workId: string;
  readonly expertEmail: string;
  readonly publishedAt: string;
  readonly verdict: SolutionVerdict;
  readonly feedback: string;
  readonly quality: {
    readonly score: number;
    readonly verdict: QualityVerdict;
    readonly strengths: string;
    readonly issues: string;
    readonly recommendations: string;
  } | null;
  readonly threads: readonly ReviewThread[];
}
const EXPERT = 'mentor.local@example.com';
const RESULT_PREFIX = 'prodguild.expert-results.v1.';
const DRAFT_PREFIX = 'prodguild.expert-drafts.v1.';
export const emptyExpertDraft = (): ExpertDraft => ({
  verdict: 'approved',
  feedback: '',
  score: null,
  qualityVerdict: 'confirmed',
  strengths: '',
  issues: '',
  recommendations: '',
});
const originalReviews: readonly OriginalReview[] = [
  {
    reviewerEmail: 'review-minus.local@example.com',
    reviewerName: 'Ревьюер−',
    verdict: 'approved',
    feedback: 'Поиск работает для обычного запроса. Решение принято без замечаний.',
  },
  {
    reviewerEmail: 'review-plus.local@example.com',
    reviewerName: 'Ревьюер+',
    verdict: 'changes_requested',
    feedback:
      'Нужно проверить границы периода и повторные события. Добавьте тесты для пустых когорт.',
  },
];
// Explicit demo ownership; never reads or modifies real participant projects.
export const expertWorks: readonly ExpertWork[] = [
  ...reviewExamples.slice(0, 2).map((example, index) => ({
    id: `reviewed-${example.id}`,
    authorEmail: 'performer.local@example.com',
    example: { ...example, id: `expert-reviewed-${example.id}` },
    review: originalReviews[index],
  })),
  ...reviewExamples.slice(0, 2).map(example => ({
    id: `new-${example.id}`,
    authorEmail: 'performer.local@example.com',
    example: { ...example, id: `expert-new-${example.id}`, threads: [] },
    review: null,
  })),
];
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function shortText(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 4000;
}
function meaningful(value: unknown): value is string {
  return shortText(value) && value.trim().length > 0;
}
function solutionVerdict(value: unknown): value is SolutionVerdict {
  return value === 'approved' || value === 'changes_requested';
}
function qualityVerdict(value: unknown): value is QualityVerdict {
  return value === 'confirmed' || value === 'needs_revision';
}
function score(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 100;
}
function validDraft(value: unknown): value is ExpertDraft {
  return (
    record(value) &&
    solutionVerdict(value['verdict']) &&
    shortText(value['feedback']) &&
    (value['score'] === null ||
      (typeof value['score'] === 'number' && Number.isFinite(value['score']))) &&
    qualityVerdict(value['qualityVerdict']) &&
    shortText(value['strengths']) &&
    shortText(value['issues']) &&
    shortText(value['recommendations'])
  );
}
function validResult(value: unknown, work: ExpertWork): value is ExpertResult {
  if (
    !record(value) ||
    value['workId'] !== work.id ||
    value['expertEmail'] !== EXPERT ||
    typeof value['publishedAt'] !== 'string' ||
    !Number.isFinite(Date.parse(value['publishedAt'])) ||
    !solutionVerdict(value['verdict']) ||
    !meaningful(value['feedback']) ||
    !validReviewDraft({ files: {}, threads: value['threads'] }, work.example)
  )
    return false;
  const quality = value['quality'];
  if (!work.review) return quality === null;
  return (
    record(quality) &&
    score(quality['score']) &&
    qualityVerdict(quality['verdict']) &&
    (quality['verdict'] !== 'confirmed' || quality['score'] >= 70) &&
    meaningful(quality['strengths']) &&
    meaningful(quality['issues']) &&
    meaningful(quality['recommendations'])
  );
}

@Injectable({ providedIn: 'root' })
export class ExpertDemoService {
  private readonly api = inject(BackendService);
  private readonly destroyRef = inject(DestroyRef);
  readonly isExpert = computed(() => this.api.user()?.email === EXPERT && !this.api.isOrganizer());
  readonly notice = signal('');
  readonly results = signal<Readonly<Record<string, ExpertResult>>>({});
  private readonly drafts = new Map<string, ExpertDraft>();
  readonly visibleWorks = computed(() => {
    const email = this.api.user()?.email;
    return expertWorks.filter(
      work => this.isExpert() || work.authorEmail === email || work.review?.reviewerEmail === email,
    );
  });

  constructor() {
    this.refresh();
    const listener = (event: StorageEvent): void => {
      if (event.key === null || event.key.startsWith(RESULT_PREFIX)) this.refresh();
    };
    window.addEventListener('storage', listener);
    this.destroyRef.onDestroy(() => window.removeEventListener('storage', listener));
  }

  refresh(): void {
    const next = { ...this.results() };
    for (const work of expertWorks) {
      try {
        const raw = localStorage.getItem(RESULT_PREFIX + work.id);
        if (raw) {
          const value: unknown = JSON.parse(raw);
          if (!validResult(value, work)) throw new Error('Invalid demo result');
          next[work.id] = value;
        }
      } catch {
        this.notice.set(
          'Не удалось восстановить часть демо-результатов. Повреждённые записи не показаны; данные текущей сессии сохранены.',
        );
      }
    }
    this.results.set(next);
  }

  loadDraft(work: ExpertWork): ExpertDraft {
    if (!this.isExpert()) return emptyExpertDraft();
    const cached = this.drafts.get(work.id);
    if (cached) return cached;
    let draft = emptyExpertDraft();
    try {
      const raw = localStorage.getItem(DRAFT_PREFIX + work.id);
      if (raw) {
        const value: unknown = JSON.parse(raw);
        if (!validDraft(value)) throw new Error('Invalid demo draft');
        draft = value;
      }
    } catch {
      this.notice.set('Черновик не восстановлен. Проверьте поля перед завершением проверки.');
    }
    this.drafts.set(work.id, draft);
    return draft;
  }

  saveDraft(work: ExpertWork, draft: ExpertDraft): void {
    if (!this.isExpert() || this.results()[work.id]) return;
    this.drafts.set(work.id, draft);
    this.write(DRAFT_PREFIX + work.id, draft);
  }

  validation(work: ExpertWork, draft: ExpertDraft): string {
    if (!meaningful(draft.feedback))
      return 'Добавьте итоговый фидбэк участнику (до 4000 символов).';
    if (work.review) {
      if (!score(draft.score)) return 'Оцените качество ревью целым баллом от 0 до 100.';
      if (draft.qualityVerdict === 'confirmed' && draft.score < 70)
        return 'Для подтверждения ревью нужно не менее 70 баллов. Выберите «На доработку».';
      if (![draft.strengths, draft.issues, draft.recommendations].every(meaningful))
        return 'Заполните сильные стороны, проблемы и рекомендации ревьюэру (до 4000 символов).';
    }
    return '';
  }

  publish(work: ExpertWork, draft: ExpertDraft, threads: readonly ReviewThread[]): string {
    if (!this.isExpert()) return 'Проверка доступна только тестовому эксперту.';
    this.refresh();
    if (this.results()[work.id])
      return 'Эта проверка уже завершена. Откройте сохранённый результат.';
    const error = this.validation(work, draft);
    if (error) return error;
    const result: ExpertResult = {
      workId: work.id,
      expertEmail: EXPERT,
      publishedAt: new Date().toISOString(),
      verdict: draft.verdict,
      feedback: draft.feedback.trim(),
      quality:
        work.review && draft.score !== null
          ? {
              score: draft.score,
              verdict: draft.qualityVerdict,
              strengths: draft.strengths.trim(),
              issues: draft.issues.trim(),
              recommendations: draft.recommendations.trim(),
            }
          : null,
      threads: structuredClone(threads),
    };
    if (!validResult(result, work)) return 'Проверьте поля и комментарии перед публикацией.';
    this.results.update(current => ({ ...current, [work.id]: result }));
    this.write(RESULT_PREFIX + work.id, result);
    return '';
  }

  verdictLabel(verdict: SolutionVerdict): string {
    return verdict === 'approved' ? 'Решение принято' : 'Нужны изменения';
  }
  private write(key: string, value: unknown): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      this.notice.set(
        'Хранилище недоступно. Изменения доступны другим демо-ролям в этой вкладке только до обновления страницы.',
      );
    }
  }
}
