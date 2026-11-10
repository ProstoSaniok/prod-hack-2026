import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { toSignal, takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiAppearance, TuiButton } from '@taiga-ui/core';
import { RepositoryEditorComponent } from '../solutions/repository-editor/repository-editor.component';
import { ReviewDemoRepository } from '../reviews/review-demo.repository';
import { ReviewThread } from '../reviews/review.models';
import {
  ExpertDemoService,
  QualityVerdict,
  SolutionVerdict,
  emptyExpertDraft,
} from './expert-demo.service';

@Component({
  selector: 'app-expert-page',
  standalone: true,
  imports: [
    DatePipe,
    RouterLink,
    ReactiveFormsModule,
    TuiAppearance,
    TuiButton,
    RepositoryEditorComponent,
  ],
  templateUrl: './expert-page.component.html',
  styleUrl: './expert-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExpertPageComponent {
  readonly demo = inject(ExpertDemoService);
  readonly discussions = inject(ReviewDemoRepository);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly id = computed(() => this.params().get('id'));
  readonly resultsMode = this.route.snapshot.data['results'] === true;
  readonly basePath = this.resultsMode ? '/demo-results' : '/expert';
  readonly selectedTab = signal<'new' | 'reviewed' | 'completed'>('new');
  readonly work = computed(() => this.demo.visibleWorks().find(work => work.id === this.id()));
  readonly result = computed(() => this.demo.results()[this.work()?.id ?? '']);
  readonly readOnly = computed(() => this.resultsMode || !!this.result());
  readonly listed = computed(() =>
    this.demo.visibleWorks().filter(work => {
      if (this.resultsMode) return true;
      const completed = !!this.demo.results()[work.id];
      return this.selectedTab() === 'completed'
        ? completed
        : !completed && (this.selectedTab() === 'reviewed' ? !!work.review : !work.review);
    }),
  );
  readonly commentExample = computed(() => {
    const work = this.work();
    return work ? { ...work.example, threads: [] } : null;
  });
  readonly selectedPath = signal('');
  readonly documents = computed(
    () =>
      this.work()?.example.files.map(file => ({ ...file, id: file.path, readOnly: true })) ?? [],
  );
  readonly content = computed(
    () => this.work()?.example.files.find(file => file.path === this.selectedPath())?.content ?? '',
  );
  readonly threads = computed(() => {
    const example = this.commentExample();
    if (!example) return [];
    return (
      this.result()?.threads ??
      (this.readOnly() ? [] : this.discussions.state('expert-demo', example).threads)
    );
  });
  readonly error = signal('');
  readonly confirming = signal(false);
  readonly replyTo = signal('');
  readonly form = new FormGroup({
    verdict: new FormControl<SolutionVerdict>('approved', { nonNullable: true }),
    feedback: new FormControl('', { nonNullable: true }),
    score: new FormControl<number | null>(null),
    qualityVerdict: new FormControl<QualityVerdict>('confirmed', { nonNullable: true }),
    strengths: new FormControl('', { nonNullable: true }),
    issues: new FormControl('', { nonNullable: true }),
    recommendations: new FormControl('', { nonNullable: true }),
  });
  readonly commentForm = new FormGroup({
    line: new FormControl(1, { nonNullable: true }),
    text: new FormControl('', { nonNullable: true }),
  });
  readonly commentError = signal('');
  private readonly commentDrafts = new Map<string, { line: number; text: string }>();

  constructor() {
    effect(() => {
      const work = this.work();
      const result = this.result();
      const readOnly = this.readOnly();
      untracked(() => {
        this.form.reset(
          result
            ? {
                verdict: result.verdict,
                feedback: result.feedback,
                score: result.quality?.score ?? null,
                qualityVerdict: result.quality?.verdict ?? 'confirmed',
                strengths: result.quality?.strengths ?? '',
                issues: result.quality?.issues ?? '',
                recommendations: result.quality?.recommendations ?? '',
              }
            : work && !readOnly
              ? this.demo.loadDraft(work)
              : emptyExpertDraft(),
          { emitEvent: false },
        );
        if (readOnly) this.form.disable({ emitEvent: false });
        else this.form.enable({ emitEvent: false });
        this.confirming.set(false);
        this.error.set('');
      });
    });
    effect(() => {
      const work = this.work();
      untracked(() => {
        this.selectedPath.set(work?.example.files[0]?.path ?? '');
        this.commentForm.reset({ line: 1, text: '' });
        this.replyTo.set('');
        this.commentDrafts.clear();
        const example = this.commentExample();
        if (example && !this.readOnly()) this.discussions.load('expert-demo', example);
      });
    });
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => {
      const work = this.work();
      if (work && !this.readOnly()) this.demo.saveDraft(work, this.form.getRawValue());
      this.confirming.set(false);
      this.error.set('');
    });
  }
  selectFile(path: string): void {
    this.commentDrafts.set(this.commentKey(), this.commentForm.getRawValue());
    this.selectedPath.set(path);
    this.replyTo.set('');
    this.commentForm.reset(this.commentDrafts.get(this.commentKey()) ?? { line: 1, text: '' });
    this.commentError.set('');
  }
  reply(thread: ReviewThread): void {
    this.commentDrafts.set(this.commentKey(), this.commentForm.getRawValue());
    this.selectedPath.set(thread.path);
    this.replyTo.set(thread.id);
    this.commentForm.reset(
      this.commentDrafts.get(this.commentKey()) ?? { line: thread.line, text: '' },
    );
  }
  cancelReply(): void {
    this.commentDrafts.set(this.commentKey(), this.commentForm.getRawValue());
    this.replyTo.set('');
    this.commentForm.reset(this.commentDrafts.get(this.commentKey()) ?? { line: 1, text: '' });
  }
  addComment(): void {
    const example = this.commentExample();
    if (!example || this.readOnly()) return;
    try {
      this.discussions.comment(
        'expert-demo',
        example,
        'Эксперт',
        this.selectedPath(),
        this.commentForm.controls.line.value,
        this.commentForm.controls.text.value,
        this.replyTo(),
      );
      this.commentDrafts.delete(this.commentKey());
      this.replyTo.set('');
      this.commentForm.reset({ line: 1, text: '' });
      this.commentError.set('');
    } catch (error) {
      this.commentError.set(
        error instanceof Error ? error.message : 'Не удалось добавить комментарий.',
      );
    }
  }
  resolve(thread: ReviewThread): void {
    const example = this.commentExample();
    if (example && !this.readOnly()) this.discussions.resolve('expert-demo', example, thread.id);
  }
  quote(thread: ReviewThread): string {
    return (
      this.work()
        ?.example.files.find(file => file.path === thread.path)
        ?.content.split('\n')[thread.line - 1] ?? ''
    );
  }
  prepare(): void {
    const work = this.work();
    if (!work || this.readOnly()) return;
    const error = this.demo.validation(work, this.form.getRawValue());
    this.error.set(error);
    this.confirming.set(!error);
  }
  publish(): void {
    const work = this.work();
    if (!work || !this.confirming() || this.readOnly()) return;
    const error = this.demo.publish(work, this.form.getRawValue(), this.threads());
    this.error.set(error);
    this.confirming.set(false);
  }
  download(): void {
    const file = this.documents().find(file => file.path === this.selectedPath());
    if (!file) return;
    const url = URL.createObjectURL(new Blob([file.content], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = file.path.split('/').at(-1) ?? 'solution.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  private commentKey(): string {
    return this.selectedPath() + ':' + this.replyTo();
  }
}
