import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { TuiButton } from '@taiga-ui/core';
import { LearningService, Project } from './learning.service';
import { projectStage } from './case-presentation';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { FileWorkspaceComponent } from './file-workspace.component';
import { LeavePageService } from '../shared/leave-page.service';
import { autoRefresh } from '../shared/auto-refresh';

@Component({
  selector: 'app-learning-project',
  standalone: true,
  imports: [
    RouterLink,
    TuiButton,
    ReactiveFormsModule,
    FileWorkspaceComponent,
    LoadingStateComponent,
  ],
  host: { '[attr.aria-busy]': 'loading()', '(window:beforeunload)': 'beforeUnload($event)' },
  template: `<header class="project-heading">
      <a routerLink="/my-cases">В работе</a>
      <h1>{{ title() || 'Работа над кейсом' }}</h1>
    </header>
    @if (error()) {
      <p role="alert">{{ error() }}</p>
    }
    @if (loading()) {
      <app-loading-state label="Открываем проект…" [skeleton]="!project()" />
    }
    @if (submitting() || refreshing()) {
      <app-loading-state [label]="submitting() ? 'Отправляем решение…' : 'Обновляем проект…'" />
    }
    @if (project(); as current) {
      @if (stage(current.status) >= 0) {
        <progress
          class="project-progress"
          [value]="stage(current.status)"
          max="2"
          aria-label="Этапы выполнения кейса"
          [attr.aria-valuetext]="stages[stage(current.status)]"
        ></progress>
      }
      <ol class="project-stages" aria-label="Этапы работы">
        @for (label of stages; track $index) {
          <li
            [class.reached]="$index <= stage(current.status)"
            [attr.aria-current]="$index === stage(current.status) ? 'step' : null"
          >
            <span aria-hidden="true">{{ $index + 1 }}</span
            >{{ label }}
          </li>
        }
      </ol>
      @if (stage(current.status) < 0) {
        <p role="status">Статус проекта недоступен.</p>
      }
      @if (current.submission_id) {
        <a tuiButton appearance="accent" [routerLink]="['/submissions', current.submission_id]">
          {{
            current.status === 'completed'
              ? 'Посмотреть результат ревью'
              : 'Статус проверки и результат'
          }}
        </a>
      }
      @if (current.status === 'active') {
        @if (confirming()) {
          <p>Отправить решение на проверку? После отправки его нельзя изменить.</p>
          @if (retrying() && !submitting()) {
            <p>Результат отправки пока неизвестен. Повторите отправку с теми же данными.</p>
          }
          @if (!service.api.user()) {
            <a routerLink="/login" [queryParams]="{ returnUrl: '/projects/' + current.id }">
              Войти и продолжить отправку
            </a>
          }
          <label for="submission-summary">Комментарий к решению (необязательно)</label>
          <textarea
            id="submission-summary"
            [formControl]="summary"
            maxlength="10000"
            [readOnly]="retrying()"
          ></textarea>
          <button
            tuiButton
            appearance="accent"
            type="button"
            [disabled]="submitting() || unsettled() || refreshing() || !service.api.user()"
            (click)="submit()"
          >
            {{
              submitting()
                ? 'Отправляем…'
                : retrying()
                  ? 'Повторить отправку'
                  : 'Подтвердить отправку'
            }}
          </button>
          <button
            tuiButton
            appearance="flat"
            type="button"
            [disabled]="submitting() || retrying()"
            (click)="cancel()"
          >
            Отмена
          </button>
        } @else {
          <button
            tuiButton
            appearance="accent"
            type="button"
            [disabled]="loading() || preparing() || refreshing()"
            (click)="prepareSubmission()"
          >
            {{ preparing() ? 'Сохраняем решение…' : 'Отправить решение' }}
          </button>
        }
      }
      <app-file-workspace
        title="Файлы решения"
        [attachmentsEnabled]="true"
        [prefix]="'/api/v1/projects/' + current.id"
        [readOnly]="current.status !== 'active' || confirming()"
        [autosave]="true"
        (unsettled)="unsettled.set($event)"
        (dirty)="dirty.set($event)"
        (changed)="refresh()"
      />
    }
    @if (error() && !confirming()) {
      <button
        tuiButton
        appearance="secondary"
        type="button"
        [disabled]="loading()"
        (click)="load()"
      >
        Повторить
      </button>
    } `,
  styles: [
    `
      :host {
        display: block;
        min-width: 0;
      }
      .project-heading {
        padding-inline-start: var(--prodguild-page-heading-inset, 0);
        min-height: 44px;
      }
      .project-stages {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        list-style: none;
        padding: 0;
        margin-block: 24px;
      }
      .project-stages li {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 12px 16px;
        border-radius: var(--prodguild-radius-sm);
        background: var(--prodguild-surface-raised);
      }
      .project-stages .reached {
        background: var(--prodguild-surface-muted);
      }
      .project-stages [aria-current='step'] {
        background: var(--prodguild-accent);
        font-weight: 500;
      }
      h1 {
        margin: 24px 0 12px;
      }
      textarea {
        display: block;
        width: 100%;
        max-width: 640px;
        min-height: 80px;
        margin: 12px 0;
        padding: 12px 16px;
        border: 0;
        border-radius: var(--prodguild-radius-sm);
        background: var(--prodguild-surface-raised);
        color: inherit;
        font: inherit;
        resize: vertical;
      }
      textarea:focus-visible {
        outline: 2px solid var(--prodguild-text);
        outline-offset: 3px;
      }
      button {
        margin: 8px 8px 8px 0;
      }
      p {
        line-height: 1.5;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LearningProjectComponent {
  readonly stages = ['В работе', 'Отправлено', 'Результат'];
  readonly stage = projectStage;
  readonly service = inject(LearningService);
  private readonly router = inject(Router);
  readonly retrying = signal(false);
  readonly confirming = signal(false);
  readonly submitting = signal(false);
  readonly preparing = signal(false);
  readonly dirty = signal(false);
  private readonly workspace = viewChild(FileWorkspaceComponent);
  private submitted = false;
  readonly unsettled = signal(true);
  readonly refreshing = signal(false);
  readonly summary = new FormControl('', { nonNullable: true });
  private attempt: { project: Project; summary: string } | null = null;
  async prepareSubmission(): Promise<void> {
    if (this.preparing() || this.submitting() || this.refreshing()) return;
    const projectId = this.project()?.id;
    if (!projectId) return;
    this.preparing.set(true);
    this.error.set('');
    try {
      if (!(await this.workspace()?.flush())) return;
      // Flushing a dirty file refreshes the project's head and increments the
      // read generation. That expected refresh must not cancel submission.
      if (this.destroyRef.destroyed || this.project()?.id !== projectId) return;
      this.confirming.set(true);
      await this.refresh();
      if (this.error()) this.confirming.set(false);
    } finally {
      this.preparing.set(false);
    }
  }
  async canLeave(): Promise<boolean> {
    if (this.submitted) return true;
    if (this.submitting()) return false;
    if (this.retrying())
      return window.confirm(
        'Отправка ещё не подтверждена. Попытка сохранится до перезагрузки страницы. Покинуть страницу?',
      );
    if (this.dirty() && !(await this.workspace()?.flush()))
      return window.confirm('Есть несохранённые изменения. Покинуть страницу?');
    return (
      !this.summary.value.trim() ||
      window.confirm('Комментарий к решению ещё не отправлен. Покинуть страницу?')
    );
  }
  beforeUnload(event: BeforeUnloadEvent): void {
    if (
      !this.submitted &&
      (this.dirty() || this.submitting() || this.retrying() || this.summary.value.trim())
    ) {
      event.preventDefault();
      event.returnValue = '';
    }
  }
  cancel(): void {
    if (this.submitting() || this.retrying()) return;
    this.confirming.set(false);
    this.attempt = null;
    this.retrying.set(false);
    this.error.set('');
  }
  async submit(): Promise<void> {
    const project = this.project();
    if (!project || this.submitting() || this.unsettled() || this.refreshing()) return;
    this.attempt ??= { project, summary: this.summary.value };
    const generation = this.generation;
    const wasUncertain = this.retrying();
    this.retrying.set(true);
    this.submitting.set(true);
    this.error.set('');
    try {
      const result = await this.service.submit(this.attempt.project, this.attempt.summary);
      if (!this.destroyRef.destroyed && generation === this.generation) {
        this.submitted = true;
        await this.router.navigate(['/submissions', result.id]);
      }
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation) {
        if (
          this.service.submissionNeedsChanges(error) ||
          (!wasUncertain && this.service.submissionRejected(error))
        ) {
          this.attempt = null;
          this.retrying.set(false);
        }
        this.error.set(this.service.message(error));
      }
    } finally {
      this.submitting.set(false);
    }
  }
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly project = signal<Project | null>(null);
  readonly title = signal('');
  readonly error = signal('');
  readonly loading = signal(false);
  private generation = 0;
  constructor() {
    autoRefresh(() => this.refresh(), {
      blocked: () =>
        this.loading() ||
        this.dirty() ||
        this.unsettled() ||
        this.confirming() ||
        this.preparing() ||
        this.submitting() ||
        this.retrying(),
      shouldPoll: () => this.project()?.status === 'submitted',
    });
    this.destroyRef.onDestroy(inject(LeavePageService).register(() => this.canLeave()));
    this.destroyRef.onDestroy(() => this.generation++);
    effect(() => {
      this.params();
      untracked(() => {
        this.project.set(null);
        this.title.set('');
        this.submitted = false;
        this.attempt = null;
        this.retrying.set(false);
        this.confirming.set(false);
        this.summary.reset('');
        this.dirty.set(false);
        this.refreshing.set(false);
        this.unsettled.set(true);
        void this.load();
      });
    });
  }
  async load(): Promise<void> {
    const generation = ++this.generation;
    this.cancel();
    this.loading.set(true);
    this.error.set('');
    try {
      const id = this.params().get('id') ?? '';
      const project = this.route.snapshot.data['startCase']
        ? await this.service.start(id)
        : await this.service.project(id);
      if (generation === this.generation) {
        this.project.set(project);
        const pending = this.service.pendingSubmission(project.id);
        if (pending) {
          this.attempt = pending;
          this.summary.setValue(pending.summary);
          this.confirming.set(true);
          this.retrying.set(true);
        }
      }
      const study = await this.service.case(project.case_id).catch(() => null);
      if (generation === this.generation && study) this.title.set(study.title);
    } catch (error) {
      if (generation === this.generation) this.error.set(this.service.message(error));
    } finally {
      if (generation === this.generation) this.loading.set(false);
    }
  }
  async refresh(): Promise<void> {
    const generation = ++this.generation;
    const project = this.project();
    if (project) {
      this.refreshing.set(true);
      this.loading.set(true);
      this.error.set('');
      try {
        const updated = await this.service.project(project.id);
        if (generation === this.generation) this.project.set(updated);
      } catch (error) {
        if (generation === this.generation) this.error.set(this.service.message(error));
      } finally {
        if (generation === this.generation) {
          this.loading.set(false);
          this.refreshing.set(false);
        }
      }
    }
  }
}
