import {
  afterEveryRender,
  ApplicationRef,
  ChangeDetectionStrategy,
  Component,
  computed,
  EmbeddedViewRef,
  inject,
  input,
  NgZone,
  output,
  signal,
  TemplateRef,
  viewChild,
} from '@angular/core';
import { StateEffect, StateField } from '@codemirror/state';
import {
  Decoration,
  DecorationSet,
  EditorView,
  lineNumbers,
  ViewPlugin,
  WidgetType,
} from '@codemirror/view';
import { TuiButton } from '@taiga-ui/core';
import { SnapshotViewerComponent } from './snapshot-viewer.component';
import { ReviewLineAnchor } from './review-diff.component';
import { Comment } from './review.service';
import { ProductIconComponent } from '../shared/product-icon.component';

/** Angular owns the form; CodeMirror owns only its position in the immutable document. */
const mountedWidgets = new WeakMap<
  HTMLElement,
  { embedded: EmbeddedViewRef<unknown>; observer: ResizeObserver }
>();

class ReviewWidget extends WidgetType {
  constructor(
    readonly key: string,
    private readonly template: TemplateRef<unknown>,
    private readonly context: unknown,
    private readonly application: ApplicationRef,
    private readonly zone: NgZone,
    private readonly mounted?: (element: HTMLElement) => void,
  ) {
    super();
  }
  override eq(other: ReviewWidget): boolean {
    return this.key === other.key && this.template === other.template;
  }
  toDOM(view: EditorView): HTMLElement {
    const element = document.createElement('div');
    element.className = 'review-file-widget';
    element.contentEditable = 'false';
    // A long code line may widen cm-content. Cards must fit the visible editor instead.
    element.style.width = '0px';
    const embedded = this.zone.run(() => {
      const embedded = this.template.createEmbeddedView(this.context);
      this.application.attachView(embedded);
      for (const node of embedded.rootNodes) element.append(node);
      embedded.detectChanges();
      return embedded;
    });
    const measure = () =>
      view.requestMeasure({
        key: element,
        read: () => {
          if (!element.isConnected || view.scrollDOM.clientWidth <= 0) return null;
          const gutter =
            view.contentDOM.getBoundingClientRect().left -
            view.scrollDOM.getBoundingClientRect().left +
            view.scrollDOM.scrollLeft -
            view.scrollDOM.clientLeft;
          return { gutter, width: Math.max(0, view.scrollDOM.clientWidth - gutter) };
        },
        write: size => {
          if (!size || !element.isConnected) return;
          const width = `${size.width}px`,
            left = `${size.gutter}px`;
          if (element.style.width !== width) element.style.width = width;
          if (element.style.left !== left) element.style.left = left;
        },
      });
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    observer.observe(view.scrollDOM);
    mountedWidgets.set(element, { embedded, observer });
    measure();
    this.mounted?.(element);
    return element;
  }
  override destroy(element: HTMLElement): void {
    const mounted = mountedWidgets.get(element);
    if (mounted) {
      mounted.observer.disconnect();
      this.application.detachView(mounted.embedded);
      mounted.embedded.destroy();
      mountedWidgets.delete(element);
    }
  }
}

@Component({
  selector: 'app-review-file-viewer',
  standalone: true,
  imports: [SnapshotViewerComponent, TuiButton, ProductIconComponent],
  templateUrl: './review-file-viewer.component.html',
  styleUrl: './review-file-viewer.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReviewFileViewerComponent {
  readonly id = input.required<string>();
  readonly comments = input<readonly Comment[]>([]);
  readonly commentAnchor = input<ReviewLineAnchor | null>(null);
  readonly composer = input<TemplateRef<unknown> | null>(null);
  readonly disabled = input(false);
  readonly lineSelected = output<ReviewLineAnchor>();
  readonly commentRemoved = output<number>();
  readonly viewer = viewChild<SnapshotViewerComponent>('viewer');
  private readonly actionTemplate = viewChild<TemplateRef<unknown>>('selectionAction');
  private readonly commentTemplate = viewChild<TemplateRef<unknown>>('savedComment');
  private readonly application = inject(ApplicationRef);
  private readonly zone = inject(NgZone);
  private readonly selection = signal<ReviewLineAnchor | null>(null);
  private editor?: EditorView;
  private binding?: object;
  private source = '';
  private gutterAnchor = 0;
  private focusedKey = '';
  private readonly setWidgets = StateEffect.define<DecorationSet>();
  private readonly widgets = StateField.define<DecorationSet>({
    create: () => Decoration.none,
    update: (value, transaction) => {
      if (transaction.docChanged) value = Decoration.none;
      for (const effect of transaction.effects)
        if (effect.is(this.setWidgets)) value = effect.value;
      return value;
    },
    provide: field => EditorView.decorations.from(field),
  });
  readonly composerVisible = computed(
    () => !!this.composer() && this.matches(this.commentAnchor()),
  );
  readonly editorExtensions = [
    this.widgets,
    ViewPlugin.define(view => {
      const binding = {};
      this.editor = view;
      this.binding = binding;
      return {
        destroy: () => {
          if (this.binding === binding) this.editor = undefined;
        },
      };
    }),
    EditorView.domEventHandlers({
      mouseup: (event, view) => {
        this.selectNative(event, view);
        return false;
      },
      keyup: (event, view) => {
        this.selectNative(event, view);
        return false;
      },
      touchend: (event, view) => {
        this.selectNative(event, view);
        return false;
      },
    }),
    lineNumbers({
      domEventHandlers: {
        mousedown: (view, block, event) => {
          if (!(event instanceof MouseEvent) || !this.canSelect(view)) return false;
          const line = view.state.doc.lineAt(block.from).number;
          if (line > this.lineCount()) return false;
          if (!event.shiftKey || !this.gutterAnchor) this.gutterAnchor = line;
          const from = Math.min(line, this.gutterAnchor),
            to = Math.max(line, this.gutterAnchor);
          view.focus();
          view.dispatch({
            selection: { anchor: view.state.doc.line(from).from, head: view.state.doc.line(to).to },
          });
          this.selectRange(view, from, to);
          event.preventDefault();
          return true;
        },
      },
    }),
    EditorView.theme({
      '.review-file-widget': {
        position: 'sticky',
        boxSizing: 'border-box',
        whiteSpace: 'normal',
        overflowWrap: 'anywhere',
        fontFamily: 'var(--prodguild-font-family, sans-serif)',
      },
      '.review-file-widget[data-review-widget=composer]': {
        padding: '16px 20px',
        background: 'var(--prodguild-surface-muted)',
      },
      '.review-file-selection': {
        background: 'color-mix(in srgb, var(--prodguild-accent) 30%, transparent)',
      },
    }),
  ];
  private signature = '';
  private previousWidgets?: DecorationSet;

  constructor() {
    afterEveryRender(() => this.renderWidgets());
  }
  private readonly lineCount = computed(() => {
    const content = this.viewer()?.content() ?? '';
    return content ? content.split('\n').length - (content.endsWith('\n') ? 1 : 0) : 0;
  });
  private matches(anchor: Comment | ReviewLineAnchor | null): boolean {
    const viewer = this.viewer();
    return (
      !!viewer &&
      !viewer.busy() &&
      !viewer.loading() &&
      !!viewer.selected() &&
      anchor?.path === viewer.selected() &&
      anchor.side === (viewer.side() === 'base' ? 'old' : 'new') &&
      Number.isInteger(anchor.line) &&
      Number.isInteger(anchor.end_line ?? anchor.line) &&
      (anchor.line ?? 0) > 0 &&
      (anchor.end_line ?? anchor.line ?? 0) >= (anchor.line ?? 0) &&
      (anchor.end_line ?? anchor.line ?? 0) <= this.lineCount()
    );
  }
  private canSelect(view: EditorView): boolean {
    const viewer = this.viewer();
    return (
      !this.disabled() &&
      !!viewer?.selected() &&
      !viewer.busy() &&
      !viewer.loading() &&
      view.state.readOnly &&
      view.state.doc.toString() === viewer.content()
    );
  }
  private selectNative(event: Event, view: EditorView): void {
    if (
      !this.canSelect(view) ||
      (event.target instanceof Element && event.target.closest('.review-file-widget'))
    )
      return;
    const selection = view.contentDOM.ownerDocument.getSelection();
    if (!selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (
      !view.contentDOM.contains(range.startContainer) ||
      !view.contentDOM.contains(range.endContainer) ||
      (range.startContainer instanceof Element
        ? range.startContainer
        : range.startContainer.parentElement
      )?.closest('.review-file-widget') ||
      (range.endContainer instanceof Element
        ? range.endContainer
        : range.endContainer.parentElement
      )?.closest('.review-file-widget')
    )
      return;
    if (range.collapsed) {
      this.zone.run(() => this.selection.set(null));
      return;
    }
    const from = view.posAtDOM(range.startContainer, range.startOffset);
    const to = view.posAtDOM(range.endContainer, range.endOffset);
    const start = Math.min(from, to),
      end = Math.max(from, to);
    if (start < 0 || end > view.state.doc.length || end <= start) {
      this.zone.run(() => this.selection.set(null));
      return;
    }
    const first = view.state.doc.lineAt(start).number;
    const last = view.state.doc.lineAt(end - 1).number;
    if (first > this.lineCount()) return;
    this.gutterAnchor = first;
    this.selectRange(view, first, Math.min(last, this.lineCount()));
  }
  private selectRange(view: EditorView, line: number, end_line: number): void {
    const viewer = this.viewer();
    if (!viewer) return;
    const anchor: ReviewLineAnchor = {
      path: viewer.selected(),
      side: viewer.side() === 'base' ? 'old' : 'new',
      line,
      end_line,
      text: view.state.doc.sliceString(
        view.state.doc.line(line).from,
        view.state.doc.line(end_line).to,
      ),
    };
    this.zone.run(() => this.selection.set(anchor));
  }
  startComment(anchor: ReviewLineAnchor): void {
    if (this.disabled() || !this.matches(anchor)) return;
    this.lineSelected.emit(anchor);
    this.selection.set(null);
  }
  private renderWidgets(): void {
    const viewer = this.viewer(),
      view = this.editor;
    if (!viewer || !view || !view.state.field(this.widgets, false)) return;
    const source = JSON.stringify([this.id(), viewer.selected(), viewer.side()]);
    if (source !== this.source) {
      this.source = source;
      this.selection.set(null);
      this.gutterAnchor = 0;
      this.focusedKey = '';
    }
    const entries: {
      line: number;
      order: number;
      key: string;
      template: TemplateRef<unknown>;
      context: unknown;
    }[] = [];
    const selection = this.selection(),
      anchor = this.commentAnchor(),
      composer = this.composer();
    const ready =
      !viewer.busy() && !viewer.loading() && view.state.doc.toString() === viewer.content();
    if (ready) {
      if (!this.disabled() && selection && this.matches(selection) && this.actionTemplate())
        entries.push({
          line: selection.end_line ?? selection.line,
          order: 1,
          key: 'selection:' + JSON.stringify(selection),
          template: this.actionTemplate()!,
          context: { $implicit: selection },
        });
      if (composer && anchor && this.matches(anchor))
        entries.push({
          line: anchor.end_line ?? anchor.line,
          order: 2,
          key: 'composer',
          template: composer,
          context: {},
        });
      this.comments().forEach((comment, index) => {
        if (this.matches(comment) && this.commentTemplate())
          entries.push({
            line: comment.end_line ?? comment.line!,
            order: index + 3,
            key: 'comment:' + index + ':' + JSON.stringify(comment),
            template: this.commentTemplate()!,
            context: { $implicit: { ...comment, index } },
          });
      });
    }
    const highlight = ready && this.matches(selection ?? anchor) ? (selection ?? anchor) : null;
    const signature = JSON.stringify([
      source,
      entries.map(entry => [entry.line, entry.key]),
      highlight ? [highlight.line, highlight.end_line] : null,
    ]);
    const current = view.state.field(this.widgets);
    if (signature === this.signature && current === this.previousWidgets) return;
    this.signature = signature;
    const ranges = entries.map(entry =>
      Decoration.widget({
        block: true,
        side: entry.order,
        widget: new ReviewWidget(
          source + ':' + entry.line + ':' + entry.key,
          entry.template,
          entry.context,
          this.application,
          this.zone,
          element => {
            const key = entry.key.startsWith('comment:')
              ? entry.key.split(':').slice(0, 2).join(':')
              : entry.key;
            element.dataset['reviewWidget'] = key;
            if (this.focusedKey === key)
              view.requestMeasure({
                read: () => element.isConnected,
                write: connected => {
                  if (connected && this.focusedKey === key) this.focusElement(element, key);
                },
              });
          },
        ),
      }).range(view.state.doc.line(entry.line).to),
    );
    if (highlight) {
      const from = view.state.doc.line(highlight.line).from;
      const to = view.state.doc.line(highlight.end_line ?? highlight.line).to;
      if (from < to)
        ranges.push(Decoration.mark({ class: 'review-file-selection' }).range(from, to));
    }
    const decorations = Decoration.set(ranges, true);
    view.dispatch({ effects: this.setWidgets.of(decorations) });
    this.previousWidgets = view.state.field(this.widgets);
  }
  private focusElement(element: HTMLElement, key: string): void {
    const target =
      key === 'composer'
        ? element.querySelector<HTMLElement>('textarea')
        : element.querySelector<HTMLElement>('[tabindex]');
    target?.focus();
    if (target && target.ownerDocument.activeElement === target) this.focusedKey = '';
  }
  private focusWidget(key: string, line: number): void {
    const view = this.editor;
    if (!view || line > this.lineCount()) return;
    this.focusedKey = key;
    view.dispatch({
      effects: EditorView.scrollIntoView(view.state.doc.line(line).to, { y: 'center' }),
    });
    view.requestMeasure({
      read: () => view.dom.querySelector<HTMLElement>(`[data-review-widget="${key}"]`),
      write: element => {
        if (element && this.focusedKey === key) this.focusElement(element, key);
      },
    });
  }
  focusComposer(): void {
    const anchor = this.commentAnchor();
    if (anchor && this.composerVisible())
      this.focusWidget('composer', anchor.end_line ?? anchor.line);
  }
  focusSavedComment(index: number): boolean {
    const comment = this.comments()[index];
    if (!comment || !this.matches(comment)) return false;
    this.focusWidget('comment:' + index, comment.end_line ?? comment.line!);
    return true;
  }
  reveal(anchor: ReviewLineAnchor): void {
    this.selection.set(null);
    this.viewer()?.reveal(anchor);
  }
}
