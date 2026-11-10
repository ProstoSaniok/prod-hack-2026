import { LoadingStateComponent } from '../shared/loading-state.component';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { TuiButton, TuiDataList, TuiTextfield } from '@taiga-ui/core';
import { autoRefresh } from '../shared/auto-refresh';
import { Assignment, QualityAssessment, Review, ReviewService, Workspace } from './review.service';
import { PublishedReviewComponent } from './published-review.component';
import { SnapshotViewerComponent } from './snapshot-viewer.component';
import { LeavePageService } from '../shared/leave-page.service';
import { WorkspaceMaximizeDirective } from '../shared/workspace-maximize/workspace-maximize.directive';
import { ProductIconComponent } from '../shared/product-icon.component';
export interface SavedQualityAssessment {
  assessment: QualityAssessment;
  actorId: string;
  sessionScope: string;
}

@Component({
  selector: 'app-review-quality',
  standalone: true,
  imports: [
    LoadingStateComponent,
    ReactiveFormsModule,
    TuiButton,
    TuiDataList,
    TuiTextfield,
    PublishedReviewComponent,
    SnapshotViewerComponent,
    WorkspaceMaximizeDirective,
    ProductIconComponent,
  ],
  templateUrl: './review-quality.component.html',
  styleUrls: ['./review-quality.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewQualityComponent {
  readonly fileView = signal<'files' | 'source-review'>('files');
  prepareAssessment(): void {
    if (!this.validateAssessment()) return;
    this.error.set('');
    this.confirming.set(true);
  }
  readonly assignment = input.required<Assignment>();
  readonly returnRequested = output<void>();
  readonly assessmentSaved = output<SavedQualityAssessment>();
  readonly decisionLabel = (value: string): string =>
    value === 'confirmed' ? 'Подтвердить ревью' : 'Вернуть ревью на доработку';
  private released = false;
  markReleased(): void {
    this.released = true;
    this.form.markAsPristine();
  }
  readonly api = inject(ReviewService);
  private readonly actorId = computed(() => this.api.learning.api.user()?.id ?? '');
  private readonly sessionScope = this.api.learning.api.sessionScope;
  private readonly destroyRef = inject(DestroyRef);
  readonly source = signal<(Review & { workspace: Workspace }) | null>(null);
  readonly error = signal('');
  readonly busy = signal(false);
  readonly loading = signal(false);
  readonly result = signal<QualityAssessment | null>(null);
  readonly confirming = signal(false);
  private generation = 0;
  private sourceAssignmentId = '';
  private sourceActorId = '';
  private sourceSessionScope = '';
  readonly form = new FormGroup({
    decision: new FormControl('confirmed', { nonNullable: true }),
    score: new FormControl<number | null>(85, [
      Validators.required,
      Validators.min(0),
      Validators.max(100),
    ]),
    feedback: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(6000)],
    }),
  });
  constructor() {
    autoRefresh(() => this.load(), {
      blocked: () =>
        this.form.dirty ||
        this.busy() ||
        this.loading() ||
        this.confirming() ||
        !!this.result() ||
        this.released,
    });
    const hasDraft = (): boolean =>
      !this.released && !this.result() && (this.form.dirty || this.busy());
    this.destroyRef.onDestroy(
      inject(LeavePageService).register(
        () =>
          !this.busy() &&
          (!hasDraft() ||
            window.confirm('Есть несохранённое заключение эксперта. Покинуть страницу?')),
        hasDraft,
      ),
    );
    effect(() => {
      this.assignment();
      this.actorId();
      this.sessionScope();
      untracked(() => void this.load());
    });
  }
  async load(): Promise<void> {
    const assignment = this.assignment();
    const actorId = this.actorId();
    const sessionScope = this.sessionScope();
    if (
      this.sourceAssignmentId === assignment.id &&
      this.sourceActorId === actorId &&
      this.sourceSessionScope === sessionScope &&
      (this.busy() || this.result() || this.released)
    )
      return;
    const generation = ++this.generation;
    const current = (): boolean =>
      !this.destroyRef.destroyed &&
      generation === this.generation &&
      actorId === this.actorId() &&
      sessionScope === this.sessionScope();
    this.loading.set(true);
    this.error.set('');
    if (
      this.sourceAssignmentId !== assignment.id ||
      this.sourceActorId !== actorId ||
      this.sourceSessionScope !== sessionScope
    ) {
      this.sourceAssignmentId = assignment.id;
      this.sourceActorId = actorId;
      this.sourceSessionScope = sessionScope;
      this.source.set(null);
      this.result.set(null);
      this.confirming.set(false);
      this.busy.set(false);
      this.released = false;
      this.form.reset({ decision: 'confirmed', score: 85, feedback: '' });
      this.fileView.set('files');
    }
    if (!actorId || !sessionScope) {
      this.loading.set(false);
      return;
    }
    try {
      const source = await this.api.source(assignment.id);
      if (
        source.workspace.assignment.id !== assignment.id ||
        source.id !== assignment.review_id ||
        source.current_revision.id !== assignment.revision_id
      )
        throw new Error('Редакция ревью изменилась. Обновите назначение перед оценкой.');
      if (current()) this.source.set(source);
    } catch (error) {
      if (current()) {
        this.source.set(null);
        this.confirming.set(false);
        this.error.set(this.api.message(error));
      }
    } finally {
      if (current()) this.loading.set(false);
    }
  }
  private validateAssessment(): boolean {
    const source = this.source();
    if (
      !source ||
      this.busy() ||
      this.loading() ||
      this.result() ||
      this.released ||
      !this.actorId() ||
      !this.sessionScope() ||
      this.sourceActorId !== this.actorId() ||
      this.sourceSessionScope !== this.sessionScope() ||
      this.form.invalid ||
      source.workspace.assignment.id !== this.assignment().id
    )
      return false;
    const { decision, score, feedback } = this.form.getRawValue();
    if (score === null || !Number.isFinite(score)) return false;
    if (!feedback.trim()) {
      this.error.set('Добавьте содержательный комментарий к экспертному решению.');
      return false;
    }
    if (decision === 'confirmed' && score < 70) {
      this.error.set('Для подтверждения ревью нужна оценка качества не ниже 70.');
      return false;
    }
    return true;
  }
  async assess(): Promise<void> {
    if (!this.confirming() || !this.validateAssessment()) return;
    const source = this.source(),
      v = this.form.getRawValue();
    if (!source || v.score === null) return;
    const generation = this.generation;
    const actorId = this.actorId();
    const sessionScope = this.sessionScope();
    const current = (): boolean =>
      !this.destroyRef.destroyed &&
      generation === this.generation &&
      actorId === this.actorId() &&
      sessionScope === this.sessionScope();
    this.busy.set(true);
    this.error.set('');
    try {
      const result = await this.api.assess(source.id, {
        assignment_id: this.assignment().id,
        revision_id: source.current_revision.id,
        decision: v.decision,
        score: v.score,
        feedback: v.feedback,
      });
      if (current()) {
        const receipt = { ...result, feedback: v.feedback };
        this.result.set(receipt);
        this.form.markAsPristine();
        this.confirming.set(false);
        this.assessmentSaved.emit({ assessment: receipt, actorId, sessionScope });
      }
    } catch (error) {
      if (current()) {
        if (error instanceof HttpErrorResponse && [401, 403, 404, 409].includes(error.status)) {
          this.source.set(null);
          this.confirming.set(false);
        }
        this.error.set(this.api.message(error));
      }
    } finally {
      if (current()) this.busy.set(false);
    }
  }
}
