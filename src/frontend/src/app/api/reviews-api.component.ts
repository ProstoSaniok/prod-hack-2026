import { NgTemplateOutlet } from '@angular/common';
import { autoRefresh } from '../shared/auto-refresh';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { ReviewQualityComponent, SavedQualityAssessment } from './review-quality.component';
import { ReviewQualityHistoryComponent } from './review-quality-history.component';
import { CompletedQualityAssessmentComponent } from './completed-quality-assessment.component';
import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { TuiButton, TuiDataList, TuiTextfield } from '@taiga-ui/core';
import {
  Assignment,
  Comment,
  Draft,
  Review,
  ReviewMeta,
  ReviewMode,
  ReviewService,
  Workspace,
} from './review.service';
import { ReviewDiffComponent, ReviewLineAnchor } from './review-diff.component';
import { ReviewFileViewerComponent } from './review-file-viewer.component';
import { PublishedReviewComponent } from './published-review.component';
import { LeavePageService } from '../shared/leave-page.service';
import { ProbationComponent } from './probation.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { WorkspaceMaximizeDirective } from '../shared/workspace-maximize/workspace-maximize.directive';

class ReviewContextError extends Error {}

@Component({
  standalone: true,
  imports: [
    LoadingStateComponent,
    RouterLink,
    ReactiveFormsModule,
    TuiButton,
    TuiDataList,
    TuiTextfield,
    ReviewFileViewerComponent,
    ReviewDiffComponent,
    PublishedReviewComponent,
    ReviewQualityComponent,
    ReviewQualityHistoryComponent,
    CompletedQualityAssessmentComponent,
    ProbationComponent,
    ProductIconComponent,
    WorkspaceMaximizeDirective,
    NgTemplateOutlet,
  ],
  templateUrl: './reviews-api.component.html',
  styleUrls: ['../cases/reviews/reviews.component.css', './reviews-api.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewsApiComponent {
  readonly api = inject(ReviewService);
  private readonly sessionContext = computed(() => {
    const actorId = this.api.learning.api.user()?.id;
    const sessionScope = this.api.learning.api.sessionScope();
    return actorId && sessionScope ? sessionScope + ':' + actorId : '';
  });
  private readonly loadedSessionContext = signal('');
  readonly qualitySessionCurrent = computed(
    () => !!this.sessionContext() && this.loadedSessionContext() === this.sessionContext(),
  );
  readonly qualityEditor = viewChild<ReviewQualityComponent>('qualityEditor');
  readonly verdictLabel = (value: string): string =>
    value === 'approved'
      ? 'Принято'
      : value === 'changes_requested'
        ? 'Нужны изменения'
        : 'Не выбран';
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  readonly fileWorkspace = viewChild<WorkspaceMaximizeDirective>('fileWorkspace');
  private readonly validationSummary = viewChild<ElementRef<HTMLElement>>('validationSummary');
  private readonly pendingCommentHelp = viewChild<ElementRef<HTMLElement>>('pendingCommentHelp');
  private readonly requestError = viewChild<ElementRef<HTMLElement>>('requestError');
  private readonly confirmation = viewChild<ElementRef<HTMLElement>>('confirmation');
  private focusAfterRender(target: () => ElementRef<HTMLElement> | undefined): void {
    afterNextRender(
      () => {
        if (!this.destroyRef.destroyed) target()?.nativeElement.focus();
      },
      { injector: this.injector },
    );
  }
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly expertMode = this.route.snapshot.routeConfig?.path?.startsWith('expert') ?? false;
  readonly assignmentKind = this.expertMode ? 'quality_check' : 'review';
  readonly basePath = this.expertMode ? '/expert' : '/reviews';
  readonly snapshot = viewChild<ReviewFileViewerComponent>('reviewSnapshot');
  readonly unavailableAssignment = signal<Assignment | null>(null);
  readonly cancelling = signal<Assignment | null>(null);
  private readonly commentBody = viewChild<ElementRef<HTMLTextAreaElement>>('commentBody');
  readonly fileView = signal<'diff' | 'all'>('diff');
  readonly diff = viewChild<ReviewDiffComponent>('reviewDiff');
  readonly allFilesOpened = signal(false);
  readonly commentQuote = signal('');
  readonly panel = signal<'overview' | 'files' | 'feedback'>('overview');
  readonly validationRequested = signal(false);
  readonly claimEmpty = signal(false);
  readonly operation = signal('');
  readonly activeAssignment = computed(() =>
    this.mode()?.tasks.find(task => this.isResumable(task)),
  );
  readonly canClaim = computed(() => this.mode()?.can_review === true);
  readonly supervised = computed(() => {
    const skills = this.workspace()?.assignment.supervised_competency_ids;
    return skills === undefined ? null : skills.length > 0;
  });
  readonly taskTitles = signal(new Map<string, string>());
  readonly visibleAssignments = computed(() =>
    this.assignments().filter(task => this.isResumable(task) && task.kind === this.assignmentKind),
  );
  isResumable(task: Assignment): boolean {
    return task.status === 'assigned';
  }
  readonly id = computed(() => this.params().get('id') ?? '');
  readonly qualityTask = signal<Assignment | null>(null);
  readonly completedQualityTask = signal<Assignment | null>(null);
  private readonly savedQualityAssessment = signal<SavedQualityAssessment | null>(null);
  readonly completedQualityReceipt = computed(() => {
    const saved = this.savedQualityAssessment();
    return saved &&
      this.qualitySessionCurrent() &&
      saved.actorId === this.api.learning.api.user()?.id &&
      saved.sessionScope === this.api.learning.api.sessionScope()
      ? saved.assessment
      : null;
  });
  readonly correction = signal<Review | null>(null);
  readonly correctionClosed = computed(() => {
    const review = this.correction();
    return (
      !!review &&
      (review.status !== 'needs_revision' || review.current_revision.draft_version === null)
    );
  });
  readonly editorLocked = computed(
    () => this.busy() || this.confirmComplete() || this.correctionClosed(),
  );
  readonly mode = signal<ReviewMode | null>(null);
  readonly assignments = signal<Assignment[]>([]);
  readonly history = signal<ReviewMeta[]>([]);
  readonly historyError = signal('');
  readonly visibleHistory = computed(() =>
    this.history().filter(review => !this.expertMode || review.status === 'published'),
  );
  readonly historyNext = signal<number | null>(null);
  readonly assignmentOffset = signal(0);
  readonly historyOffset = signal(0);
  readonly workspace = signal<Workspace | null>(null);
  readonly draft = signal<Draft | null>(null);
  readonly comments = signal<Comment[]>([]);
  readonly published = signal<Review | null>(null);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  readonly confirmComplete = signal(false);
  readonly serverDraft = signal<Draft | null>(null);
  readonly localCopy = signal<{
    content: Omit<Draft, 'version'>;
    comment: {
      body: string;
      path: string;
      line: number | null;
      end_line: number | null;
      side: 'old' | 'new';
    };
    quote: string;
  } | null>(null);
  readonly showProbation = signal(false);
  private completed = false;
  readonly form = new FormGroup({
    score: new FormControl<number | null>(null, [Validators.min(0), Validators.max(100)]),
    verdict: new FormControl('', { nonNullable: true }),
    strengths: new FormControl('', { nonNullable: true, validators: [Validators.maxLength(6000)] }),
    problems: new FormControl('', { nonNullable: true, validators: [Validators.maxLength(6000)] }),
    next_steps: new FormControl('', {
      nonNullable: true,
      validators: [Validators.maxLength(6000)],
    }),
  });
  readonly commentForm = new FormGroup({
    body: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(1000)],
    }),
    path: new FormControl('', { nonNullable: true }),
    line: new FormControl<number | null>(null),
    end_line: new FormControl<number | null>(null),
    side: new FormControl<'old' | 'new'>('new', { nonNullable: true }),
  });
  private readonly changes = toSignal(this.form.valueChanges);
  private readonly commentChanges = toSignal(this.commentForm.valueChanges);
  readonly commentAnchor = computed<ReviewLineAnchor | null>(() => {
    this.commentChanges();
    const value = this.commentForm.getRawValue();
    return value.path && value.line !== null
      ? {
          path: value.path,
          line: value.line,
          end_line: value.end_line ?? value.line,
          side: value.side,
          text: this.commentQuote(),
        }
      : null;
  });
  private focusComment(): void {
    afterNextRender(
      () => {
        if (this.destroyRef.destroyed) return;
        if (this.fileView() === 'diff' && this.diff()?.composerVisible())
          this.diff()?.focusComposer();
        else if (this.fileView() === 'all' && this.snapshot()?.composerVisible())
          this.snapshot()?.focusComposer();
        else this.commentBody()?.nativeElement.focus();
      },
      { injector: this.injector },
    );
  }
  readonly dirty = computed(() => {
    this.changes();
    const d = this.draft();
    return d
      ? JSON.stringify(this.content()) !==
          JSON.stringify({
            score: d.score,
            verdict: d.verdict,
            feedback: d.feedback,
            comments: d.comments,
          })
      : false;
  });
  readonly requirements = computed(() => {
    this.changes();
    const value = this.form.getRawValue();
    const scoreValid =
      value.score !== null &&
      Number.isInteger(value.score) &&
      value.score >= 0 &&
      value.score <= 100;
    return [
      { label: 'Оценка — целое число от 0 до 100', met: scoreValid },
      {
        label: 'Вердикт: «Принято» от 70 баллов, «Нужны изменения» — ниже 70',
        met:
          scoreValid &&
          ((value.score !== null && value.score >= 70 && value.verdict === 'approved') ||
            (value.score !== null && value.score < 70 && value.verdict === 'changes_requested')),
      },
      {
        label: 'Сильные стороны — что получилось хорошо',
        met: !!value.strengths.trim() && value.strengths.length <= 6000,
      },
      {
        label: 'Проблемы — что исправить; если их нет, напишите об этом',
        met: !!value.problems.trim() && value.problems.length <= 6000,
      },
      {
        label: 'Следующие шаги — конкретные рекомендации автору',
        met: !!value.next_steps.trim() && value.next_steps.length <= 6000,
      },
    ];
  });
  readonly completedRequirements = computed(
    () => this.requirements().filter(item => item.met).length,
  );
  readonly pendingComment = computed(() => {
    this.commentChanges();
    return !!this.commentForm.controls.body.value.trim();
  });
  readonly readyToSend = computed(
    () => this.completedRequirements() === 5 && !this.pendingComment(),
  );
  readonly canComplete = computed(() => {
    const draft = this.draft(),
      correction = this.correction();
    return (
      !!draft &&
      this.readyToSend() &&
      !this.dirty() &&
      !this.correctionClosed() &&
      (!correction ||
        (correction.current_revision.draft_version !== null &&
          draft.version > correction.current_revision.draft_version))
    );
  });
  private generation = 0;
  constructor() {
    autoRefresh(() => this.refreshReadModels(), {
      blocked: () =>
        this.busy() || this.loading() || (!!this.id() && !this.route.snapshot.data['published']),
      shouldPoll: () => !this.id() || this.published()?.status === 'pending_quality_check',
    });
    this.destroyRef.onDestroy(
      inject(LeavePageService).register(
        () => this.canLeave(),
        () =>
          !this.completed &&
          !!this.workspace() &&
          (this.dirty() || this.pendingComment() || !!this.localCopy() || this.busy()),
      ),
    );
    effect(() => {
      this.params();
      this.sessionContext();
      untracked(() => void this.load());
    });
  }
  private canLeave(): boolean {
    if (this.completed || !this.workspace()) return true;
    if (this.busy()) return false;
    return (
      (!this.dirty() && !this.pendingComment() && !this.localCopy()) ||
      window.confirm('Есть несохранённые изменения ревью. Покинуть страницу?')
    );
  }
  private content(): Omit<Draft, 'version'> {
    const v = this.form.getRawValue();
    return {
      score: v.score,
      verdict: v.verdict || null,
      feedback: { strengths: v.strengths, problems: v.problems, next_steps: v.next_steps },
      comments: this.comments(),
    };
  }
  private apply(draft: Draft): void {
    this.form.reset({ score: draft.score, verdict: draft.verdict ?? '', ...draft.feedback });
    this.comments.set(draft.comments);
    this.draft.set(draft);
    this.serverDraft.set(null);
    this.confirmComplete.set(false);
  }
  private matchesCorrection(review: Review, assignment: Assignment): boolean {
    return (
      assignment.kind === 'review' &&
      assignment.status === 'completed' &&
      review.assignment_id === assignment.id &&
      review.submission_id === assignment.submission_id &&
      review.reviewer_id === this.api.learning.api.user()?.id
    );
  }
  async load(): Promise<void> {
    this.completed = false;
    const generation = ++this.generation;
    const sessionContext = this.sessionContext();
    const current = (): boolean =>
      !this.destroyRef.destroyed &&
      generation === this.generation &&
      sessionContext === this.sessionContext();
    if (this.loadedSessionContext() !== sessionContext) {
      this.mode.set(null);
      this.assignments.set([]);
      this.history.set([]);
      this.taskTitles.set(new Map());
      this.historyNext.set(null);
      this.cancelling.set(null);
    }
    this.loadedSessionContext.set(sessionContext);
    this.loading.set(true);
    this.busy.set(false);
    this.error.set('');
    this.historyError.set('');
    this.notice.set('');
    this.claimEmpty.set(false);
    this.validationRequested.set(false);
    this.panel.set('overview');
    this.fileView.set('diff');
    this.allFilesOpened.set(false);
    this.commentQuote.set('');
    this.commentForm.reset({ body: '', path: '', line: null, end_line: null, side: 'new' });
    this.workspace.set(null);
    this.qualityTask.set(null);
    this.completedQualityTask.set(null);
    this.savedQualityAssessment.set(null);
    this.unavailableAssignment.set(null);
    this.correction.set(null);
    this.published.set(null);
    this.draft.set(null);
    this.confirmComplete.set(false);
    this.serverDraft.set(null);
    this.localCopy.set(null);
    if (!sessionContext) {
      this.loading.set(false);
      this.error.set('Сессия истекла. Войдите снова.');
      return;
    }
    try {
      if (this.id()) {
        if (this.route.snapshot.data['published']) {
          const result = await this.api.review(this.id());
          if (current()) this.published.set(result);
        } else {
          const assignment = await this.api.assignment(this.id());
          if (!current()) return;
          const correctionId = this.params().get('reviewId');
          const correction = correctionId ? await this.api.review(correctionId) : null;
          if (!current()) return;
          if (
            correction &&
            (!this.matchesCorrection(correction, assignment) ||
              correction.status !== 'needs_revision')
          )
            throw new ReviewContextError(
              'Это ревью недоступно для исправления по указанному назначению. Откройте его из раздела «Мои ревью».',
            );
          if (!correction && assignment.kind === 'review' && assignment.status !== 'assigned') {
            if (current()) this.unavailableAssignment.set(assignment);
            return;
          }
          if (assignment.kind !== this.assignmentKind && !this.params().has('reviewId')) {
            await this.router.navigate(
              [assignment.kind === 'quality_check' ? '/expert' : '/reviews', assignment.id],
              { replaceUrl: true },
            );
            return;
          }
          if (assignment.kind === 'quality_check' && assignment.status === 'completed') {
            if (current()) this.completedQualityTask.set(assignment);
            return;
          }
          if (!assignment.can_continue) {
            if (current()) this.unavailableAssignment.set(assignment);
            return;
          }
          if (assignment.kind === 'quality_check') {
            if (current()) this.qualityTask.set(assignment);
            return;
          }
          const workspace = await this.api.workspace(assignment.id);
          if (correction && !this.matchesCorrection(correction, workspace.assignment))
            throw new ReviewContextError(
              'Назначение изменилось. Откройте исправление из раздела «Мои ревью».',
            );
          if (current()) {
            this.workspace.set(workspace);
            this.correction.set(correction);
            this.apply(workspace.draft);
          }
        }
      } else {
        const [mode, assignments, history] = await Promise.all([
          this.api.mode(),
          this.api.assignments(this.assignmentOffset()),
          (this.expertMode
            ? Promise.resolve({ items: [] as ReviewMeta[], next_offset: null })
            : this.api.reviews(this.historyOffset())
          ).catch(error => {
            if (current()) this.historyError.set(this.api.message(error));
            return { items: [] as ReviewMeta[], next_offset: null };
          }),
        ]);
        if (current()) {
          this.mode.set(mode);
          this.api.learning.api.applyReviewAccess(mode);
          this.assignments.set(assignments);
          this.history.set(history.items);
          this.historyNext.set(history.next_offset);
        }
        if (!current()) return;
        const titles = await Promise.allSettled(
          assignments
            .filter(task => task.can_continue)
            .map(async task => {
              const workspace =
                task.kind === 'quality_check'
                  ? (await this.api.source(task.id)).workspace
                  : await this.api.workspace(task.id);
              return [task.id, workspace.case.title] as const;
            }),
        );
        if (current()) {
          this.taskTitles.set(
            new Map(
              titles.flatMap(result => (result.status === 'fulfilled' ? [result.value] : [])),
            ),
          );
        }
      }
    } catch (error) {
      if (current()) {
        this.confirmComplete.set(false);
        this.error.set(
          error instanceof ReviewContextError ? error.message : this.api.message(error),
        );
        this.focusAfterRender(() => this.requestError());
      }
    } finally {
      if (current()) this.loading.set(false);
    }
  }
  qualityAssessmentSaved(task: Assignment, receipt: SavedQualityAssessment): void {
    if (
      this.qualityTask()?.id !== task.id ||
      this.id() !== task.id ||
      !this.qualitySessionCurrent() ||
      receipt.actorId !== this.api.learning.api.user()?.id ||
      receipt.sessionScope !== this.api.learning.api.sessionScope()
    )
      return;
    this.completed = true;
    this.savedQualityAssessment.set(receipt);
    this.completedQualityTask.set({ ...task, status: 'completed', can_continue: false });
    this.qualityTask.set(null);
  }
  private async run(action: (apply: (update: () => void) => void) => Promise<void>): Promise<void> {
    if (this.busy() || this.loading()) return;
    const generation = this.generation;
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      await action(update => {
        if (!this.destroyRef.destroyed && generation === this.generation) update();
      });
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation) {
        this.confirmComplete.set(false);
        this.error.set(
          error instanceof ReviewContextError ? error.message : this.api.message(error),
        );
        this.focusAfterRender(() => this.requestError());
      }
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) {
        this.busy.set(false);
        this.operation.set('');
      }
    }
  }
  async claim(): Promise<void> {
    const generation = this.generation;
    await this.run(async apply => {
      this.claimEmpty.set(false);
      this.operation.set('Подбираем ревью…');
      const mode = await this.api.mode();
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      apply(() => {
        this.mode.set(mode);
        this.assignments.set([...mode.tasks]);
        this.api.learning.api.applyReviewAccess(mode);
      });
      const existing = mode.tasks.find(task => this.isResumable(task));
      if (existing) {
        apply(() => {
          if (existing.can_continue)
            void this.router.navigate([
              existing.kind === 'quality_check' ? '/expert' : '/reviews',
              existing.id,
            ]);
          else this.unavailableAssignment.set(existing);
        });
        return;
      }
      if (!mode.can_review) return;
      if (!this.mode()?.enabled) {
        const mode = await this.api.setMode(true);
        if (this.destroyRef.destroyed || generation !== this.generation) return;
        apply(() => this.mode.set(mode));
      }
      const task = await this.api.claim();
      apply(() => {
        if (task)
          void this.router.navigate([
            task.kind === 'quality_check' ? '/expert' : '/reviews',
            task.id,
          ]);
        else this.claimEmpty.set(true);
      });
    });
  }
  async prepareComplete(): Promise<void> {
    if (this.editorLocked() || this.loading()) return;
    this.panel.set('feedback');
    this.validationRequested.set(true);
    this.form.markAllAsTouched();
    if (!this.readyToSend()) {
      this.focusAfterRender(() => this.validationSummary() ?? this.pendingCommentHelp());
      return;
    }
    const draft = this.draft();
    if (!draft) return;
    const content = this.content();
    await this.run(async apply => {
      this.operation.set('Сохраняем ревью…');
      const correction = this.correction();
      const needsSave =
        this.dirty() ||
        (correction &&
          correction.current_revision.draft_version !== null &&
          draft.version <= correction.current_revision.draft_version);
      const saved = needsSave ? await this.api.save(this.id(), content, draft.version) : draft;
      apply(() => {
        this.apply(saved);
        this.confirmComplete.set(true);
        this.focusAfterRender(() => this.confirmation());
      });
    });
  }
  async save(): Promise<void> {
    const draft = this.draft();
    if (!draft || this.form.invalid || this.editorLocked()) return;
    const content = this.content();
    await this.run(async apply => {
      const result = await this.api.save(this.id(), content, draft.version);
      apply(() => {
        this.apply(result);
        this.notice.set('Черновик сохранён на сервере');
      });
    });
  }
  async reloadDraft(): Promise<void> {
    await this.run(async apply => {
      apply(() => {
        this.confirmComplete.set(false);
        this.serverDraft.set(null);
      });
      const correction = this.correction();
      if (correction) {
        const current = await this.api.review(correction.id);
        const assignment = this.workspace()?.assignment;
        if (!assignment || !this.matchesCorrection(current, assignment))
          throw new ReviewContextError(
            'Ревью не соответствует назначению. Ваш текст сохранён на странице.',
          );
        apply(() => this.correction.set(current));
        if (
          current.status !== 'needs_revision' ||
          current.current_revision.draft_version === null
        ) {
          apply(() =>
            this.notice.set(
              'Статус ревью изменился. Отправка закрыта; ваш текст остался на странице.',
            ),
          );
          return;
        }
      }
      const draft = await this.api.draft(this.id());
      apply(() => this.serverDraft.set(draft));
    });
  }
  acceptServerDraft(): void {
    const draft = this.serverDraft();
    if (!draft || this.busy()) return;
    if (this.localCopy()) {
      this.error.set(
        'Сначала проверьте сохранённую копию и явно удалите её, прежде чем заменять ещё один черновик.',
      );
      return;
    }
    this.localCopy.set({
      content: this.content(),
      comment: this.commentForm.getRawValue(),
      quote: this.commentQuote(),
    });
    this.apply(draft);
    this.serverDraft.set(null);
    this.error.set('');
    this.notice.set(
      'Загружен серверный черновик. Локальную копию можно восстановить до ухода со страницы.',
    );
  }
  restoreLocalCopy(): void {
    const copy = this.localCopy();
    if (!copy || this.busy()) return;
    const current = {
      content: this.content(),
      comment: this.commentForm.getRawValue(),
      quote: this.commentQuote(),
    };
    this.form.reset({
      score: copy.content.score,
      verdict: copy.content.verdict ?? '',
      ...copy.content.feedback,
    });
    this.comments.set(copy.content.comments);
    this.commentForm.reset(copy.comment);
    this.commentQuote.set(copy.quote);
    this.localCopy.set(current);
    this.confirmComplete.set(false);
    this.serverDraft.set(null);
    this.notice.set(
      'Копия восстановлена. Предыдущий текст сохранён в другой копии. Сверьте изменения перед сохранением на сервере.',
    );
  }
  selectLine(anchor: ReviewLineAnchor): void {
    if (this.editorLocked()) return;
    if (this.comments().length >= 20) {
      this.error.set('Достигнут лимит: 20 комментариев. Удалите лишний, чтобы добавить новый.');
      return;
    }
    if (this.pendingComment()) {
      this.error.set(
        'Сначала добавьте текущий комментарий или очистите его текст, затем выберите другую строку.',
      );
      this.focusComment();
      return;
    }
    this.error.set('');
    this.commentForm.patchValue({
      path: anchor.path,
      line: anchor.line,
      end_line: anchor.end_line ?? anchor.line,
      side: anchor.side,
    });
    this.commentQuote.set(anchor.text);
    this.focusComment();
  }
  selectFileView(view: 'diff' | 'all'): void {
    if (view === 'all') this.allFilesOpened.set(true);
    this.fileView.set(view);
  }
  clearAnchor(): void {
    if (this.editorLocked()) return;
    this.commentForm.patchValue({ path: '', line: null, end_line: null, side: 'new' });
    this.commentQuote.set('');
    this.focusComment();
  }
  async addComment(): Promise<void> {
    if (this.editorLocked() || this.commentForm.invalid || this.comments().length >= 20) return;
    const draft = this.draft(),
      v = this.commentForm.getRawValue();
    if (!draft || !v.body.trim()) return;
    if (this.form.invalid) {
      this.error.set(
        'Комментарий пока не сохранён. Исправьте оценку (0–100) или слишком длинный текст обратной связи.',
      );
      return;
    }
    const comment: Comment = {
      body: v.body,
      path: v.path || null,
      line: v.path ? v.line : null,
      side: v.path ? v.side : null,
      ...(v.path && v.end_line !== null && v.end_line !== v.line ? { end_line: v.end_line } : {}),
    };
    const content = { ...this.content(), comments: [...this.comments(), comment] };
    await this.run(async apply => {
      const saved = await this.api.save(this.id(), content, draft.version);
      apply(() => {
        this.apply(saved);
        this.commentForm.reset({ body: '', path: '', line: null, end_line: null, side: 'new' });
        this.commentQuote.set('');
        this.notice.set('Комментарий сохранён в черновике');
        const index = saved.comments.length - 1;
        afterNextRender(
          () => {
            if (this.destroyRef.destroyed) return;
            const focused =
              this.fileView() === 'diff'
                ? this.diff()?.focusSavedComment(index)
                : this.snapshot()?.focusSavedComment(index);
            if (!focused) this.commentBody()?.nativeElement.focus();
          },
          { injector: this.injector },
        );
      });
    });
  }
  async removeComment(index: number): Promise<void> {
    const draft = this.draft();
    if (!draft || this.editorLocked()) return;
    if (this.form.invalid) {
      this.error.set('Исправьте оценку или слишком длинную обратную связь перед сохранением.');
      return;
    }
    const content = { ...this.content(), comments: this.comments().filter((_, i) => i !== index) };
    await this.run(async apply => {
      const saved = await this.api.save(this.id(), content, draft.version);
      apply(() => this.apply(saved));
    });
  }
  openComment(comment: Comment): void {
    if (!comment.path || !comment.line || !comment.side) return;
    this.panel.set('files');
    this.selectFileView('all');
    afterNextRender(
      () => {
        void this.snapshot()?.reveal({
          path: comment.path ?? '',
          line: comment.line ?? 1,
          end_line: comment.end_line ?? comment.line ?? 1,
          side: comment.side ?? 'new',
          text: comment.body,
        });
      },
      { injector: this.injector },
    );
  }
  async refreshQueue(): Promise<void> {
    if (this.id() || this.busy() || this.loading()) return;
    const generation = this.generation;
    try {
      const [mode, assignments, history] = await Promise.all([
        this.api.mode(),
        this.api.assignments(this.assignmentOffset()),
        this.expertMode
          ? Promise.resolve({ items: [] as ReviewMeta[], next_offset: null })
          : this.api
              .reviews(this.historyOffset())
              .catch(() => ({ items: this.history(), next_offset: this.historyNext() })),
      ]);
      if (this.destroyRef.destroyed || generation !== this.generation || this.id()) return;
      this.mode.set(mode);
      this.api.learning.api.applyReviewAccess(mode);
      this.assignments.set(assignments);
      this.history.set(history.items);
      this.historyNext.set(history.next_offset);
    } catch {
      /* Keep the current queue visible during a transient read failure. */
    }
  }
  private async refreshReadModels(): Promise<void> {
    if (!this.id()) return this.refreshQueue();
    if (!this.route.snapshot.data['published'] || this.busy() || this.loading()) return;
    const generation = this.generation,
      id = this.id();
    try {
      const review = await this.api.review(id);
      if (!this.destroyRef.destroyed && generation === this.generation && this.id() === id)
        this.published.set(review);
    } catch {
      /* Preserve the current published snapshot through transient read failures. */
    }
  }
  async cancelAssignment(): Promise<void> {
    const assignment = this.cancelling();
    if (!assignment) return;
    await this.run(async apply => {
      await this.api.cancel(assignment.id);
      apply(() => {
        this.completed = true;
        this.qualityEditor()?.markReleased();
        this.cancelling.set(null);
        this.unavailableAssignment.set(null);
      });
    });
    if (this.cancelling()) return;
    if (this.id()) await this.router.navigate([this.basePath]);
    else await this.load();
  }
  async complete(): Promise<void> {
    const draft = this.draft();
    if (!draft || !this.confirmComplete() || !this.canComplete()) return;
    await this.run(async apply => {
      this.operation.set('Отправляем ревью…');
      const correction = this.correction();
      const result = correction
        ? await this.api.revise(correction.id, correction.current_revision.id, draft.version)
        : await this.api.complete(this.id(), draft.version);
      apply(() => {
        this.completed = true;
        void this.router.navigate(['/review-results', result.id]);
      });
    });
    if (this.completed) await this.api.learning.api.refreshWallet();
  }
  async assignmentPage(direction: number): Promise<void> {
    this.assignmentOffset.update(v => Math.max(0, v + direction * 20));
    await this.load();
  }
  async historyPage(direction: number): Promise<void> {
    this.historyOffset.update(v => Math.max(0, v + direction * 20));
    await this.load();
  }
}
