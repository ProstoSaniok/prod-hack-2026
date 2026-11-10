import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  ElementRef,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiAppearance, TuiButton, TuiTextfield } from '@taiga-ui/core';
import { MockWorkspaceService } from '../data/mock-workspace.service';
import { FileTreeComponent } from '../solutions/file-tree/file-tree.component';
import {
  EditorDocument,
  RepositoryEditorComponent,
} from '../solutions/repository-editor/repository-editor.component';
import { ExpertFeedbackComponent } from '../expert/expert-feedback.component';
import { ReviewDemoRepository } from './review-demo.repository';
import { reviewExamples } from './review-examples';
import { ReviewInputError, ReviewThread } from './review.models';

@Component({
  selector: 'app-reviews',
  standalone: true,
  imports: [
    RouterLink,
    ExpertFeedbackComponent,
    ReactiveFormsModule,
    TuiAppearance,
    TuiButton,
    TuiTextfield,
    FileTreeComponent,
    RepositoryEditorComponent,
  ],
  templateUrl: './reviews.component.html',
  styleUrl: './reviews.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewsComponent {
  readonly examples = reviewExamples;
  readonly repository = inject(ReviewDemoRepository);
  private readonly session = inject(MockWorkspaceService);
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, {
    initialValue: this.route.snapshot.paramMap,
  });
  readonly reviewId = computed(() => this.params().get('id'));
  readonly example = computed(() => this.examples.find(item => item.id === this.reviewId()));
  private readonly owner = computed(() => this.session.user()?.login ?? '');
  readonly state = computed(() => {
    const example = this.example();
    return example ? this.repository.state(this.owner(), example) : null;
  });
  readonly panel = signal<'overview' | 'files'>('files');
  readonly version = signal<'source' | 'changes'>('changes');
  readonly selectedPath = signal('');
  readonly selectedFile = computed(() =>
    this.example()?.files.find(file => file.path === this.selectedPath()),
  );
  readonly documents = computed<readonly EditorDocument[]>(() =>
    (this.example()?.files ?? []).map(file => ({
      id: file.path,
      path: file.path,
      content: this.state()?.files[file.path] ?? file.content,
      modified: (this.state()?.files[file.path] ?? file.content) !== file.content,
    })),
  );
  readonly content = computed(() => {
    const source = this.selectedFile();
    return this.version() === 'source'
      ? (source?.content ?? '')
      : (this.state()?.files[this.selectedPath()] ?? source?.content ?? '');
  });
  readonly changedCount = computed(() => this.documents().filter(file => file.modified).length);
  readonly threads = computed(() =>
    (this.state()?.threads ?? []).filter(thread => thread.path === this.selectedPath()),
  );
  readonly openCount = computed(
    () => this.state()?.threads.filter(thread => !thread.resolved).length ?? 0,
  );
  readonly lineCount = computed(() => this.selectedFile()?.content.split('\n').length ?? 1);
  readonly form = new FormGroup({
    line: new FormControl(1, { nonNullable: true }),
    text: new FormControl('', { nonNullable: true }),
  });
  readonly replyTo = signal('');
  readonly commentError = signal('');
  readonly commentField = signal<'line' | 'text'>('text');
  readonly notice = signal('');
  private readonly textarea = viewChild<ElementRef<HTMLTextAreaElement>>('commentInput');
  private readonly draftComments = new Map<string, { line: number; text: string }>();

  constructor() {
    effect(() => {
      const example = this.example();
      const owner = this.owner();
      untracked(() => {
        if (example) this.repository.load(owner, example);
        this.selectedPath.set(example?.files[0]?.path ?? '');
        this.panel.set('files');
        this.version.set('changes');
        this.replyTo.set('');
        this.form.reset({ line: 1, text: '' });
        this.notice.set('');
        this.commentError.set('');
      });
    });
  }

  selectFile(path: string): void {
    this.stashComment();
    this.selectedPath.set(path);
    this.replyTo.set('');
    this.restoreComment();
  }

  edit(content: string): void {
    const example = this.example();
    if (!example || this.version() === 'source' || !this.selectedFile()) return;
    this.repository.edit(this.owner(), example, this.selectedPath(), content);
    this.notice.set('');
  }

  save(): void {
    const example = this.example();
    if (!example || this.version() === 'source') return;
    this.repository.save(this.owner(), example);
    this.notice.set(
      this.repository.memoryOnly()
        ? 'Правки сохранены в текущей сессии.'
        : 'Правки сохранены в этом браузере.',
    );
  }

  download(): void {
    const file = this.selectedFile();
    if (!file) return;
    const url = URL.createObjectURL(
      new Blob([this.content()], { type: 'text/plain;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = file.path.split('/').at(-1) ?? 'review.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  reply(thread: ReviewThread): void {
    this.stashComment();
    this.replyTo.set(thread.id);
    this.restoreComment(thread.line);
    this.focusComment();
  }

  cancelReply(): void {
    this.stashComment();
    this.replyTo.set('');
    this.restoreComment();
  }

  addComment(): void {
    const example = this.example();
    if (!example) return;
    try {
      this.repository.comment(
        this.owner(),
        example,
        this.session.user()?.name ?? 'Ревьюер',
        this.selectedPath(),
        this.form.controls.line.value,
        this.form.controls.text.value,
        this.replyTo(),
      );
      this.draftComments.delete(this.commentKey());
      const replied = this.replyTo();
      this.replyTo.set('');
      if (replied) this.restoreComment();
      else this.form.controls.text.reset();
      this.commentError.set('');
      this.notice.set('Комментарий добавлен в демо-ревью.');
    } catch (error: unknown) {
      this.commentField.set(error instanceof ReviewInputError ? error.field : 'text');
      this.commentError.set(
        error instanceof Error ? error.message : 'Не удалось добавить комментарий.',
      );
    }
  }

  resolve(thread: ReviewThread): void {
    const example = this.example();
    if (example) this.repository.resolve(this.owner(), example, thread.id);
  }

  quote(thread: ReviewThread): string {
    return (
      this.example()
        ?.files.find(file => file.path === thread.path)
        ?.content.split('\n')[thread.line - 1] ?? ''
    );
  }

  focusComment(): void {
    this.textarea()?.nativeElement.focus();
  }

  showDiscussions(): void {
    this.panel.set('files');
    requestAnimationFrame(() => {
      document.getElementById('review-discussions')?.scrollIntoView({ block: 'start' });
      this.focusComment();
    });
  }

  private commentKey(): string {
    return `${this.owner()}:${this.reviewId()}:${this.selectedPath()}:${this.replyTo()}`;
  }
  private stashComment(): void {
    this.draftComments.set(this.commentKey(), this.form.getRawValue());
  }
  private restoreComment(line = 1): void {
    this.form.reset(this.draftComments.get(this.commentKey()) ?? { line, text: '' });
    this.commentError.set('');
  }
}
