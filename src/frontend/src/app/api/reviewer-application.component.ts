import { autoRefresh } from '../shared/auto-refresh';
import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton, TuiDataList, TuiTextfield } from '@taiga-ui/core';
import { LeavePageService } from '../shared/leave-page.service';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import {
  ApplicationDetail,
  DevelopmentService,
  PublishedEvidence,
  ReadinessAssessment,
} from './development.service';
import { object } from './learning.service';

@Component({
  standalone: true,
  imports: [
    DatePipe,
    RouterLink,
    ReactiveFormsModule,
    TuiButton,
    TuiDataList,
    TuiTextfield,
    LoadingStateComponent,
    ProductIconComponent,
  ],
  templateUrl: './reviewer-application.component.html',
  styleUrl: './workflow-panels.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewerApplicationComponent {
  readonly decisionLabel = (value: string): string =>
    value === 'approved' ? 'Подтвердить готовность' : 'Рекомендовать дальнейшее развитие';
  readonly service = inject(DevelopmentService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly detail = signal<ApplicationDetail | null>(null);
  readonly evidence = signal<PublishedEvidence | null>(null);
  readonly selectedAssessment = signal<ReadinessAssessment | null>(null);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly contentLoading = signal(false);
  readonly error = signal('');
  readonly contentError = signal('');
  readonly notice = signal('');
  readonly dirty = signal(false);
  readonly pending = signal(false);
  readonly stale = signal(false);
  readonly confirming = signal(false);
  readonly form = new FormGroup({
    decision: new FormControl<'approved' | 'needs_development'>('approved', { nonNullable: true }),
    feedback: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(6000), Validators.pattern(/\S/)],
    }),
  });
  private generation = 0;
  constructor() {
    autoRefresh(() => this.load(), {
      blocked: () =>
        this.loading() || this.contentLoading() || this.busy() || this.dirty() || this.pending(),
      shouldPoll: () => this.detail()?.status === 'pending',
    });
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => this.dirty.set(true));
    const unregister = inject(LeavePageService).register(
      () =>
        !this.busy() &&
        (!(this.dirty() || this.pending()) ||
          window.confirm(
            'Есть неотправленный комментарий или незавершённый запрос. Покинуть заявку?',
          )),
      () => this.dirty() || this.pending() || this.busy(),
    );
    this.destroyRef.onDestroy(unregister);
    effect(() => {
      this.params();
      untracked(() => {
        this.detail.set(null);
        this.evidence.set(null);
        this.selectedAssessment.set(null);
        this.form.reset({ decision: 'approved', feedback: '' }, { emitEvent: false });
        this.dirty.set(false);
        void this.load();
      });
    });
  }
  async load(): Promise<void> {
    if (this.busy()) return;
    const id = this.params().get('id') ?? '';
    const generation = ++this.generation;
    this.loading.set(true);
    this.error.set('');
    this.confirming.set(false);
    this.pending.set(this.service.mutations.pending(this.service.assessmentAction(id)));
    if (this.pending()) {
      const payload = this.service.mutations.pendingBody(this.service.assessmentAction(id));
      const body =
        payload && typeof payload === 'object' && !Array.isArray(payload) ? object(payload) : null;
      if (
        body &&
        (body['decision'] === 'approved' || body['decision'] === 'needs_development') &&
        typeof body['feedback'] === 'string'
      ) {
        this.form.setValue(
          { decision: body['decision'], feedback: body['feedback'] },
          { emitEvent: false },
        );
      }
    }
    try {
      const detail = await this.service.application(id);
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      this.detail.set(detail);
      this.stale.set(false);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation) {
        this.error.set(this.service.message(error));
        if (error instanceof HttpErrorResponse && (error.status === 403 || error.status === 404)) {
          this.detail.set(null);
          this.evidence.set(null);
          this.selectedAssessment.set(null);
        }
      }
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.loading.set(false);
    }
  }
  async openEvidence(reviewId: string): Promise<void> {
    const detail = this.detail();
    if (!detail || this.contentLoading()) return;
    const generation = this.generation;
    this.contentLoading.set(true);
    this.contentError.set('');
    try {
      const content = await this.service.evidence(detail.id, reviewId);
      if (!this.destroyRef.destroyed && generation === this.generation) this.evidence.set(content);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.contentError.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed) this.contentLoading.set(false);
    }
  }
  async openAssessment(id: string): Promise<void> {
    const detail = this.detail();
    if (!detail || this.contentLoading()) return;
    const generation = this.generation;
    this.contentLoading.set(true);
    this.contentError.set('');
    try {
      const content = await this.service.assessment(detail.id, id);
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.selectedAssessment.set(content);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.contentError.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed) this.contentLoading.set(false);
    }
  }
  prepare(): void {
    if (this.form.valid && !this.busy() && !this.pending() && !this.stale())
      this.confirming.set(true);
    else this.form.markAllAsTouched();
  }
  async submit(retry = false): Promise<void> {
    const detail = this.detail();
    if (
      !detail ||
      this.busy() ||
      (!retry && (!detail.can_assess || this.form.invalid || this.stale()))
    )
      return;
    this.busy.set(true);
    this.error.set('');
    this.notice.set('');
    try {
      if (retry) await this.service.mutations.retry(this.service.assessmentAction(detail.id));
      else
        await this.service.assess(
          detail,
          this.form.controls.decision.value,
          this.form.controls.feedback.value,
        );
      if (this.destroyRef.destroyed) return;
      this.dirty.set(false);
      this.confirming.set(false);
      this.notice.set('Решение эксперта сохранено.');
    } catch (error) {
      if (!this.destroyRef.destroyed) {
        this.error.set(this.service.message(error));
        this.stale.set(error instanceof HttpErrorResponse && error.status === 409);
        this.confirming.set(false);
      }
    } finally {
      if (!this.destroyRef.destroyed) {
        this.pending.set(this.service.mutations.pending(this.service.assessmentAction(detail.id)));
        this.busy.set(false);
      }
    }
    if (this.notice() && !this.destroyRef.destroyed) await this.load();
  }
}
