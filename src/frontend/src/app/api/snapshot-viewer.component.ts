import { EditorLineSelection } from '../cases/solutions/code-editor/code-editor.component';
import { ReviewLineAnchor } from './review-diff.component';
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
import { FileTreeComponent } from '../cases/solutions/file-tree/file-tree.component';
import { RepositoryEditorComponent } from '../cases/solutions/repository-editor/repository-editor.component';
import { TuiButton } from '@taiga-ui/core';
import { DiffPage, FilePage, LearningService, LinePage } from './learning.service';
import { ProductIconComponent } from '../shared/product-icon.component';
import { WorkspaceMaximizeDirective } from '../shared/workspace-maximize/workspace-maximize.directive';
import { Extension } from '@codemirror/state';
@Component({
  selector: 'app-snapshot-viewer',
  standalone: true,
  imports: [
    LoadingStateComponent,
    TuiButton,
    FileTreeComponent,
    RepositoryEditorComponent,
    ProductIconComponent,
  ],
  hostDirectives: [WorkspaceMaximizeDirective],
  templateUrl: './snapshot-viewer.component.html',
  styleUrls: ['../cases/reviews/reviews.component.css', './snapshot-viewer.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SnapshotViewerComponent {
  readonly maximize = inject(WorkspaceMaximizeDirective);
  readonly service = inject(LearningService);
  private readonly destroyRef = inject(DestroyRef);
  readonly id = input.required<string>();
  readonly commentable = input(false);
  readonly commentSelected = output<ReviewLineAnchor>();
  readonly selection = signal<EditorLineSelection | null>(null);
  readonly focusLines = signal<{ line: number; end_line: number } | null>(null);
  readonly resource = input<'submissions' | 'review-assignments'>('submissions');
  readonly allowMaximize = input(true);
  readonly editorExtensions = input<Extension>([]);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly side = signal<'base' | 'head'>('head');
  readonly files = signal<FilePage>({ entries: [], next_offset: null });
  readonly diff = signal<DiffPage>({ entries: [], next_offset: null });
  readonly lines = signal<LinePage>({ lines: [], next_offset: null });
  readonly fileOffset = signal(0);
  readonly diffOffset = signal(0);
  readonly lineOffset = signal(0);
  readonly lineHistory = signal<number[]>([]);
  readonly linePath = signal('');
  readonly selected = signal('');
  readonly content = signal('');
  readonly documents = computed(() => {
    const paths = this.files().entries.map(file => file.path);
    // A saved comment can point beyond the currently loaded file-list page.
    if (this.selected() && !paths.includes(this.selected())) paths.push(this.selected());
    return paths.map(path => ({
      id: path,
      path,
      content: path === this.selected() ? this.content() : '',
      readOnly: true,
    }));
  });
  private readonly pendingReveal = signal<ReviewLineAnchor | null>(null);
  private generation = 0;
  constructor() {
    effect(() => {
      this.id();
      this.resource();
      untracked(() => void this.load());
    });
    effect(() => {
      const anchor = this.pendingReveal();
      if (!anchor || this.loading() || this.busy()) return;
      untracked(() => {
        this.pendingReveal.set(null);
        void this.revealReady(anchor);
      });
    });
  }
  status(value: string): string {
    return value === 'added' ? 'Добавлен' : value === 'deleted' ? 'Удалён' : 'Изменён';
  }
  async load(): Promise<void> {
    const generation = ++this.generation;
    this.loading.set(true);
    this.busy.set(false);
    this.error.set('');
    this.selected.set('');
    this.pendingReveal.set(null);
    this.linePath.set('');
    this.side.set('head');
    this.selection.set(null);
    try {
      const [files, diff] = await Promise.all([
        this.service.submissionFiles(this.id(), 'head', 0, this.resource()),
        this.service.submissionDiff(this.id(), 0, this.resource()),
      ]);
      if (this.destroyRef.destroyed || generation !== this.generation) return;
      this.files.set(files);
      this.diff.set(diff);
      this.fileOffset.set(0);
      this.diffOffset.set(0);
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.loading.set(false);
    }
  }
  private async run(
    action: (id: string, apply: (update: () => void) => void) => Promise<void>,
  ): Promise<void> {
    const item = { id: this.id() };
    if (!item.id || this.busy() || this.loading()) return;
    const generation = this.generation;
    this.busy.set(true);
    this.error.set('');
    try {
      await action(item.id, update => {
        if (!this.destroyRef.destroyed && generation === this.generation) update();
      });
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.service.message(error));
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.busy.set(false);
    }
  }
  async loadFiles(offset: number): Promise<void> {
    await this.run(async (id, apply) => {
      const files = await this.service.submissionFiles(id, this.side(), offset, this.resource());
      apply(() => {
        this.files.set(files);
        this.fileOffset.set(offset);
        this.selected.set('');
        this.content.set('');
      });
    });
  }
  async changeSide(side: 'base' | 'head'): Promise<void> {
    await this.run(async (id, apply) => {
      const files = await this.service.submissionFiles(id, side, 0, this.resource());
      apply(() => {
        this.side.set(side);
        this.selection.set(null);
        this.files.set(files);
        this.fileOffset.set(0);
        this.selected.set('');
        this.content.set('');
      });
    });
  }
  selectLines(selection: EditorLineSelection | null): void {
    this.selection.set(selection);
  }
  commentSelection(): void {
    const selection = this.selection();
    if (selection && this.selected() && this.commentable() && !this.busy())
      this.commentSelected.emit({
        ...selection,
        path: this.selected(),
        side: this.side() === 'base' ? 'old' : 'new',
      });
  }
  reveal(anchor: ReviewLineAnchor): void {
    this.pendingReveal.set(anchor);
  }
  private async revealReady(anchor: ReviewLineAnchor): Promise<void> {
    await this.run(async (id, apply) => {
      const side = anchor.side === 'old' ? 'base' : 'head';
      const files =
        side !== this.side()
          ? await this.service.submissionFiles(id, side, 0, this.resource())
          : null;
      const file = await this.service.submissionFile(id, anchor.path, side, this.resource());
      apply(() => {
        if (files) {
          this.files.set(files);
          this.fileOffset.set(0);
        }
        this.side.set(side);
        this.selected.set(anchor.path);
        this.content.set(file.content ?? '');
        this.selection.set(null);
        this.focusLines.set({ line: anchor.line, end_line: anchor.end_line ?? anchor.line });
      });
    });
  }
  async openFile(path: string): Promise<void> {
    this.selection.set(null);
    this.focusLines.set(null);
    if (!path) {
      this.selected.set('');
      this.content.set('');
      return;
    }
    await this.run(async (id, apply) => {
      const file = await this.service.submissionFile(id, path, this.side(), this.resource());
      apply(() => {
        this.selected.set(path);
        this.content.set(file.content ?? '');
      });
    });
  }
  async loadDiff(offset: number): Promise<void> {
    await this.run(async (id, apply) => {
      const diff = await this.service.submissionDiff(id, offset, this.resource());
      apply(() => {
        this.diff.set(diff);
        this.diffOffset.set(offset);
        this.linePath.set('');
      });
    });
  }
  async openLines(path: string): Promise<void> {
    await this.run(async (id, apply) => {
      const lines = await this.service.submissionLines(id, path, 0, this.resource());
      apply(() => {
        this.lines.set(lines);
        this.linePath.set(path);
        this.lineOffset.set(0);
        this.lineHistory.set([]);
      });
    });
  }
  async nextLines(): Promise<void> {
    const offset = this.lines().next_offset;
    if (offset === null) return;
    await this.run(async (id, apply) => {
      const lines = await this.service.submissionLines(
        id,
        this.linePath(),
        offset,
        this.resource(),
      );
      apply(() => {
        this.lineHistory.update(history => [...history, this.lineOffset()]);
        this.lineOffset.set(offset);
        this.lines.set(lines);
      });
    });
  }
  async previousLines(): Promise<void> {
    const offset = this.lineHistory().at(-1);
    if (offset === undefined) return;
    await this.run(async (id, apply) => {
      const lines = await this.service.submissionLines(
        id,
        this.linePath(),
        offset,
        this.resource(),
      );
      apply(() => {
        this.lineHistory.update(history => history.slice(0, -1));
        this.lineOffset.set(offset);
        this.lines.set(lines);
      });
    });
  }
  downloadFile(): void {
    if (!this.selected() || this.busy() || this.loading()) return;
    const url = URL.createObjectURL(
      new Blob([this.content()], { type: 'text/plain;charset=utf-8' }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = this.selected().split('/').at(-1) ?? 'solution.txt';
    link.click();
    URL.revokeObjectURL(url);
  }
}
