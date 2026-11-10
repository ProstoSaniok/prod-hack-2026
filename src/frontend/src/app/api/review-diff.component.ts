import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  TemplateRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { TuiButton } from '@taiga-ui/core';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { DiffPage, LearningService, LinePage } from './learning.service';
import { Comment } from './review.service';

export interface ReviewLineAnchor {
  path: string;
  line: number;
  end_line?: number;
  side: 'old' | 'new';
  text: string;
}

@Component({
  selector: 'app-review-diff',
  standalone: true,
  imports: [TuiButton, LoadingStateComponent, ProductIconComponent, NgTemplateOutlet],
  templateUrl: './review-diff.component.html',
  styleUrl: './review-diff.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewDiffComponent {
  private readonly api = inject(LearningService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  readonly id = input.required<string>();
  readonly comments = input<readonly Comment[]>([]);
  readonly disabled = input(false);
  readonly composer = input<TemplateRef<unknown> | null>(null);
  readonly commentAnchor = input<ReviewLineAnchor | null>(null);
  readonly selection = signal<ReviewLineAnchor | null>(null);
  readonly selectionNotice = signal('');
  readonly composerRow = computed(() => this.anchorRow(this.commentAnchor()));
  readonly composerVisible = computed(() => !!this.composer() && this.composerRow() >= 0);
  readonly selectionRow = computed(() => this.anchorRow(this.selection()));
  readonly lineSelected = output<ReviewLineAnchor>();
  readonly commentRemoved = output<number>();
  readonly files = signal<DiffPage>({ entries: [], next_offset: null });
  readonly lines = signal<LinePage>({ lines: [], next_offset: null });
  readonly path = signal('');
  readonly fileOffset = signal(0);
  readonly lineOffset = signal(0);
  readonly lineHistory = signal<number[]>([]);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly reviewed = signal<ReadonlySet<string>>(new Set());
  readonly currentFile = computed(() =>
    this.files().entries.find(file => file.comment_path === this.path()),
  );
  readonly rows = computed(() =>
    this.lines().lines.map((line, row) => ({
      ...line,
      comments: this.comments().flatMap((comment, index) =>
        this.anchorRow(comment) === row ? [{ ...comment, index }] : [],
      ),
    })),
  );
  private generation = 0;
  private rangeStart: { line: number; side: 'old' | 'new'; path: string } | null = null;

  private anchorRow(anchor: Comment | ReviewLineAnchor | null): number {
    if (!anchor?.path || anchor.path !== this.path() || !anchor.line || !anchor.side) return -1;
    const side = anchor.side;
    const end = anchor.end_line ?? anchor.line;
    return this.lines().lines.findIndex(
      line => (side === 'old' ? line.old_line : line.new_line) === end,
    );
  }

  private clearSelection(): void {
    this.rangeStart = null;
    this.selection.set(null);
    this.selectionNotice.set('');
  }

  highlighted(line: LinePage['lines'][number]): boolean {
    const anchor = this.selection() ?? this.commentAnchor();
    if (!anchor || anchor.path !== this.path()) return false;
    const position = anchor.side === 'old' ? line.old_line : line.new_line;
    return (
      position !== null && position >= anchor.line && position <= (anchor.end_line ?? anchor.line)
    );
  }

  focusComposer(): void {
    this.host.nativeElement
      .querySelector<HTMLTextAreaElement>('.inline-composer textarea')
      ?.focus();
  }

  focusSavedComment(index: number): boolean {
    const comment = this.host.nativeElement.querySelector<HTMLElement>(
      `[data-comment-index="${index}"]`,
    );
    comment?.focus();
    return !!comment;
  }

  startComment(): void {
    const anchor = this.selection();
    if (!anchor || this.busy() || this.disabled()) return;
    this.lineSelected.emit(anchor);
    this.selection.set(null);
  }

  selectText(event: MouseEvent | KeyboardEvent): void {
    if (this.busy() || this.disabled()) return;
    if (
      event.target instanceof Element &&
      event.target.closest('button, textarea, input, .inline-comment, .inline-action')
    )
      return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const codeAt = (node: Node): HTMLElement | null => {
      const element = node instanceof Element ? node : node.parentElement;
      const code = element?.closest<HTMLElement>('code[data-diff-line]') ?? null;
      return code && this.host.nativeElement.contains(code) ? code : null;
    };
    const first = codeAt(range.startContainer);
    const last = codeAt(range.endContainer);
    if (!first || !last) return;
    let start = Number(first.dataset['diffLine']);
    let end = Number(last.dataset['diffLine']);
    const tail = range.cloneRange();
    tail.selectNodeContents(first);
    tail.setStart(range.startContainer, range.startOffset);
    if (!tail.toString().length) start++;
    const head = range.cloneRange();
    head.selectNodeContents(last);
    head.setEnd(range.endContainer, range.endOffset);
    if (!head.toString().length) end--;
    const lines = this.lines().lines.slice(start, end + 1);
    if (!lines.length) return;
    const side = lines.every(line => line.new_line !== null)
      ? 'new'
      : lines.every(line => line.old_line !== null)
        ? 'old'
        : null;
    this.clearSelection();
    if (!side) {
      this.selectionNotice.set(
        'Выделите строки одной версии или выберите диапазон по номерам слева либо справа.',
      );
      return;
    }
    const from = side === 'old' ? lines[0].old_line : lines[0].new_line;
    const to = side === 'old' ? lines[lines.length - 1].old_line : lines[lines.length - 1].new_line;
    if (from === null || to === null) return;
    this.rangeStart = { line: from, side, path: this.path() };
    this.selection.set({
      path: this.path(),
      line: from,
      end_line: to,
      side,
      text: lines.map(line => line.text).join('\n'),
    });
  }

  constructor() {
    effect(() => {
      this.id();
      untracked(() => {
        this.generation++;
        this.files.set({ entries: [], next_offset: null });
        this.lines.set({ lines: [], next_offset: null });
        this.path.set('');
        this.clearSelection();
        this.reviewed.set(new Set());
        this.busy.set(false);
        void this.loadFiles(0);
      });
    });
  }

  status(status: string): string {
    return status === 'added' ? 'Добавлен' : status === 'deleted' ? 'Удалён' : 'Изменён';
  }

  count(path: string): number {
    return this.comments().filter(comment => comment.path === path).length;
  }

  private async run(action: (apply: (update: () => void) => void) => Promise<void>): Promise<void> {
    if (this.busy()) return;
    const generation = this.generation;
    this.busy.set(true);
    this.error.set('');
    try {
      await action(update => {
        if (!this.destroyRef.destroyed && generation === this.generation) update();
      });
    } catch (error) {
      if (!this.destroyRef.destroyed && generation === this.generation)
        this.error.set(this.api.message(error));
    } finally {
      if (!this.destroyRef.destroyed && generation === this.generation) this.busy.set(false);
    }
  }

  async loadFiles(offset: number): Promise<void> {
    const generation = this.generation;
    await this.run(async apply => {
      const files = await this.api.submissionDiff(this.id(), offset, 'review-assignments');
      apply(() => {
        this.files.set(files);
        this.fileOffset.set(offset);
        this.path.set('');
        this.lines.set({ lines: [], next_offset: null });
        this.clearSelection();
      });
    });
    if (
      !this.destroyRef.destroyed &&
      generation === this.generation &&
      !this.error() &&
      !this.path()
    ) {
      const first = this.files().entries[0];
      if (first) await this.openFile(first.comment_path);
    }
  }

  async openFile(path: string): Promise<void> {
    await this.run(async apply => {
      const lines = await this.api.submissionLines(this.id(), path, 0, 'review-assignments');
      apply(() => {
        this.path.set(path);
        this.lines.set(lines);
        this.lineOffset.set(0);
        this.lineHistory.set([]);
        this.clearSelection();
      });
    });
  }

  async pageLines(next: boolean): Promise<void> {
    const offset = next ? this.lines().next_offset : this.lineHistory().at(-1);
    if (offset === null || offset === undefined) return;
    await this.run(async apply => {
      const lines = await this.api.submissionLines(
        this.id(),
        this.path(),
        offset,
        'review-assignments',
      );
      apply(() => {
        this.lineHistory.update(history =>
          next ? [...history, this.lineOffset()] : history.slice(0, -1),
        );
        this.lineOffset.set(offset);
        this.lines.set(lines);
        this.clearSelection();
      });
    });
  }

  selectLine(line: LinePage['lines'][number], side: 'old' | 'new', extend = false): void {
    const position = side === 'old' ? line.old_line : line.new_line;
    if (position === null || this.busy() || this.disabled()) return;
    const previous = this.rangeStart;
    if (!extend || !previous || previous.side !== side || previous.path !== this.path())
      this.rangeStart = { line: position, side, path: this.path() };
    const start = this.rangeStart?.line ?? position;
    this.selectionNotice.set('');
    this.selection.set({
      path: this.path(),
      line: Math.min(start, position),
      end_line: Math.max(start, position),
      side,
      text: this.lines()
        .lines.filter(item => {
          const value = side === 'old' ? item.old_line : item.new_line;
          return (
            value !== null &&
            value >= Math.min(start, position) &&
            value <= Math.max(start, position)
          );
        })
        .map(item => item.text)
        .join('\n'),
    });
  }

  toggleReviewed(): void {
    this.reviewed.update(previous => {
      const next = new Set(previous);
      if (next.has(this.path())) next.delete(this.path());
      else next.add(this.path());
      return next;
    });
  }
}
