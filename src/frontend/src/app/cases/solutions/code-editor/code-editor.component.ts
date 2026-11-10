import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  Injector,
  input,
  NgZone,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { basicSetup } from 'codemirror';
import { Compartment, EditorState, Extension, Transaction } from '@codemirror/state';
import { EditorView, keymap, lineNumbers } from '@codemirror/view';
import { javascript } from '@codemirror/lang-javascript';
import { python } from '@codemirror/lang-python';
import { sql } from '@codemirror/lang-sql';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';

export interface EditorLineSelection {
  line: number;
  end_line: number;
  text: string;
}

function language(path: string): Extension {
  switch (path.split('.').at(-1)?.toLowerCase()) {
    case 'ts':
    case 'tsx':
      return javascript({ typescript: true, jsx: path.endsWith('tsx') });
    case 'js':
    case 'jsx':
      return javascript({ jsx: path.endsWith('jsx') });
    case 'py':
      return python();
    case 'sql':
      return sql();
    case 'json':
      return json();
    case 'md':
      return [markdown(), EditorView.lineWrapping];
    default:
      return [];
  }
}

@Component({
  selector: 'app-code-editor',
  standalone: true,
  template: '<div #editor class="editor-host"></div>',
  styles: [
    ':host { display: flex; flex: 1; min-width: 0; min-height: 0; } .editor-host { flex: 1; min-width: 0; min-height: 0; }',
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CodeEditorComponent {
  private readonly zone = inject(NgZone);
  private readonly injector = inject(Injector);
  readonly content = input.required<string>();
  readonly path = input.required<string>();
  readonly documentId = input('');
  readonly readOnly = input(false);
  readonly editorExtensions = input<Extension>([]);
  readonly contentChange = output<string>();
  readonly save = output<void>();
  readonly selectionEnabled = input(false);
  readonly focusLines = input<{ line: number; end_line: number } | null>(null);
  readonly lineSelection = output<EditorLineSelection | null>();
  private gutterAnchor = 0;
  private readonly host = viewChild.required<ElementRef<HTMLDivElement>>('editor');
  private readonly ready = signal(false);
  private readonly language = new Compartment();
  private readonly access = new Compartment();
  private readonly label = new Compartment();
  private readonly extensions = new Compartment();
  private view?: EditorView;
  private resizeObserver?: ResizeObserver;
  private readonly documents = new Map<
    string,
    { state: EditorState; scroll: ReturnType<EditorView['scrollSnapshot']> }
  >();
  private currentPath = '';
  private currentReadOnly = false;
  private currentExtensions: Extension | undefined;
  private lastEmittedContent: string | undefined;
  private updating = false;

  constructor() {
    afterNextRender(() => this.ready.set(true));
    inject(DestroyRef).onDestroy(() => {
      this.resizeObserver?.disconnect();
      this.view?.destroy();
    });
    effect(() => {
      const target = this.focusLines();
      this.documentId();
      if (!this.ready() || !target) return;
      afterNextRender(
        () => {
          const view = this.view;
          if (!view || this.focusLines() !== target || target.line > view.state.doc.lines) return;
          const doc = view.state.doc;
          view.dispatch({
            selection: {
              anchor: doc.line(target.line).from,
              head: doc.line(Math.min(target.end_line, doc.lines)).to,
            },
            scrollIntoView: true,
          });
        },
        { injector: this.injector },
      );
    });
    effect(() => {
      const content = this.content();
      const path = this.path();
      const identity = this.documentId() || path;
      const readOnly = this.readOnly();
      const extensions = this.editorExtensions();
      if (!this.ready()) return;
      const attributes = this.attributes(path, readOnly);
      const access = this.accessExtensions(readOnly);
      if (!this.view) {
        this.currentPath = identity;
        this.currentReadOnly = readOnly;
        this.currentExtensions = extensions;
        this.view = this.zone.runOutsideAngular(
          () =>
            new EditorView({
              parent: this.host().nativeElement,
              state: this.editorState(content, path, readOnly, extensions),
            }),
        );
        this.zone.runOutsideAngular(() => {
          this.resizeObserver = new ResizeObserver(() => this.view?.requestMeasure());
          this.resizeObserver.observe(this.host().nativeElement);
        });
        return;
      }
      this.updating = true;
      try {
        if (this.currentPath !== identity) {
          // Preserve each file's history without allowing Undo to cross document boundaries.
          this.documents.set(this.currentPath, {
            // Keep core history/caret, but never cache consumer callbacks or annotations.
            state: this.view.state.update({
              effects: this.extensions.reconfigure([]),
              filter: false,
            }).state,
            scroll: this.view.scrollSnapshot(),
          });
          // A blurred native range still points at the previous file's DOM. While
          // the next file loads read-only, it must not replace its cached caret.
          const selection = this.view.contentDOM.ownerDocument.getSelection();
          if (
            !this.view.hasFocus &&
            selection?.anchorNode &&
            this.view.contentDOM.contains(selection.anchorNode)
          )
            selection.removeAllRanges();
          const cached = this.documents.get(identity);
          const restore = cached?.state.doc.toString() === content ? cached : undefined;
          // Install current callbacks before setState can create any consumer ViewPlugin.
          const state = restore
            ? restore.state.update({
                effects: [
                  this.access.reconfigure(access),
                  this.label.reconfigure(attributes),
                  this.extensions.reconfigure(extensions),
                ],
                filter: false,
              }).state
            : this.editorState(content, path, readOnly, extensions);
          this.view.setState(state);
          if (restore) this.view.dispatch({ effects: restore.scroll });
          this.currentPath = identity;
          this.gutterAnchor = 0;
          this.currentReadOnly = readOnly;
          this.currentExtensions = extensions;
          this.lastEmittedContent = undefined;
        } else {
          if (this.currentReadOnly !== readOnly || this.currentExtensions !== extensions) {
            // New callbacks must be active before synchronizing external content.
            this.view.dispatch({
              effects: [
                this.access.reconfigure(access),
                this.label.reconfigure(attributes),
                this.extensions.reconfigure(extensions),
              ],
              filter: false,
            });
            this.currentReadOnly = readOnly;
            this.currentExtensions = extensions;
          }
          if (content !== this.lastEmittedContent && this.view.state.doc.toString() !== content) {
            this.view.dispatch({
              changes: { from: 0, to: this.view.state.doc.length, insert: content },
              // Loading a server version is not a user edit that Undo may erase.
              annotations: Transaction.addToHistory.of(false),
            });
          }
        }
      } finally {
        this.updating = false;
      }
    });
  }

  private attributes(path: string, readOnly: boolean): Extension {
    return EditorView.contentAttributes.of({
      'aria-label': `Редактор: ${path}`,
      'aria-readonly': String(readOnly),
      spellcheck: 'false',
      tabindex: '0',
    });
  }

  private accessExtensions(readOnly: boolean): Extension {
    return [
      EditorState.readOnly.of(readOnly),
      EditorView.editable.of(!readOnly),
      EditorView.editorAttributes.of({ 'data-readonly': String(readOnly) }),
    ];
  }

  private syncNativeSelection(event: InputEvent, view: EditorView): void {
    const selection = view.contentDOM.ownerDocument.getSelection();
    const range =
      event.getTargetRanges?.()[0] ?? (selection?.rangeCount ? selection.getRangeAt(0) : undefined);
    if (
      !range ||
      !view.contentDOM.contains(range.startContainer) ||
      !view.contentDOM.contains(range.endContainer)
    )
      return;

    const from = view.posAtDOM(range.startContainer, range.startOffset);
    const to = view.posAtDOM(range.endContainer, range.endOffset);
    let anchor = from;
    let head = to;
    if (
      selection?.anchorNode &&
      selection.focusNode &&
      view.contentDOM.contains(selection.anchorNode) &&
      view.contentDOM.contains(selection.focusNode)
    ) {
      const nativeAnchor = view.posAtDOM(selection.anchorNode, selection.anchorOffset);
      const nativeHead = view.posAtDOM(selection.focusNode, selection.focusOffset);
      if (
        Math.min(nativeAnchor, nativeHead) === from &&
        Math.max(nativeAnchor, nativeHead) === to
      ) {
        anchor = nativeAnchor;
        head = nativeHead;
      }
    }
    const current = view.state.selection.main;
    if (current.anchor !== anchor || current.head !== head) {
      // Native selection can change immediately before input, before selectionchange fires.
      view.dispatch({ selection: { anchor, head }, userEvent: 'select' });
    }
  }

  private emitLines(view: EditorView, from: number, to: number): void {
    if (!this.selectionEnabled()) return;
    const doc = view.state.doc;
    const realLines =
      doc.length === 0 ? 0 : doc.lines - (doc.sliceString(doc.length - 1) === '\n' ? 1 : 0);
    if (!realLines) {
      this.zone.run(() => this.lineSelection.emit(null));
      return;
    }
    const start = Math.min(from, to);
    const end = Math.max(from, to);
    const line = Math.min(realLines, doc.lineAt(start).number);
    const endLine = Math.min(realLines, doc.lineAt(end > start ? end - 1 : end).number);
    const text = doc.sliceString(doc.line(line).from, doc.line(endLine).to).slice(0, 1000);
    this.zone.run(() => this.lineSelection.emit({ line, end_line: endLine, text }));
  }

  private nativeLines(view: EditorView): void {
    if (!this.selectionEnabled()) return;
    const selection = view.contentDOM.ownerDocument.getSelection();
    if (!selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (
      !view.contentDOM.contains(range.startContainer) ||
      !view.contentDOM.contains(range.endContainer)
    )
      return;
    this.emitLines(
      view,
      view.posAtDOM(range.startContainer, range.startOffset),
      view.posAtDOM(range.endContainer, range.endOffset),
    );
  }

  private editorState(
    content: string,
    path: string,
    readOnly: boolean,
    extensions: Extension,
  ): EditorState {
    return EditorState.create({
      doc: content,
      extensions: [
        basicSetup,
        EditorState.lineSeparator.of('\n'),
        lineNumbers({
          domEventHandlers: {
            mousedown: (view, block, event) => {
              if (!this.selectionEnabled() || !(event instanceof MouseEvent)) return false;
              const line = view.state.doc.lineAt(block.from).number;
              if (!event.shiftKey || !this.gutterAnchor) this.gutterAnchor = line;
              const start = Math.min(this.gutterAnchor, line),
                end = Math.max(this.gutterAnchor, line);
              const from = view.state.doc.line(start).from,
                to = view.state.doc.line(end).to;
              view.focus();
              view.dispatch({ selection: { anchor: from, head: to } });
              this.emitLines(view, from, to);
              event.preventDefault();
              return true;
            },
          },
        }),
        EditorState.phrases.of({
          Find: 'Найти',
          Replace: 'Заменить',
          next: 'Далее',
          previous: 'Назад',
          all: 'Выбрать все',
          'match case': 'Учитывать регистр',
          regexp: 'Регулярное выражение',
          'by word': 'Слово целиком',
          replace: 'Заменить',
          'replace all': 'Заменить все',
          close: 'Закрыть',
          'Go to line': 'Перейти к строке',
          go: 'Перейти',
          'current match': 'Текущее совпадение',
          'on line': 'в строке',
          Completions: 'Дополнения',
          'No diagnostics': 'Нет замечаний',
          Diagnostics: 'Замечания',
          'Control character': 'Управляющий символ',
        }),
        this.language.of(language(path)),
        this.access.of(this.accessExtensions(readOnly)),
        this.label.of(this.attributes(path, readOnly)),
        this.extensions.of(extensions),
        EditorView.domEventHandlers({
          touchend: (_event, view) => {
            this.nativeLines(view);
            return false;
          },
          mouseup: (_event, view) => {
            this.nativeLines(view);
            return false;
          },
          keyup: (_event, view) => {
            this.nativeLines(view);
            return false;
          },
          beforeinput: (event, view) => {
            if (
              event.inputType !== 'insertText' ||
              !event.cancelable ||
              event.isComposing ||
              view.compositionStarted ||
              event.data === null ||
              view.state.readOnly
            )
              return false;
            // Commit ordinary typing and its caret together. A scroll event can otherwise
            // flush the native DOM mutation before CodeMirror reads the new selection.
            // Composition stays on CodeMirror's native IME handling path.
            this.syncNativeSelection(event, view);
            const text = event.data;
            const { from, to } = view.state.selection.main;
            const insert = () =>
              view.state.update(view.state.replaceSelection(text), {
                scrollIntoView: true,
                userEvent: 'input.type',
              });
            // Preserve public input extensions such as bracket closing and selection wrapping.
            for (const handler of view.state.facet(EditorView.inputHandler)) {
              if (handler(view, from, to, text, insert)) return true;
            }
            view.dispatch(insert());
            return true;
          },
        }),
        keymap.of([
          {
            key: 'Mod-s',
            run: () => {
              this.zone.run(() => this.save.emit());
              return true;
            },
          },
        ]),
        EditorView.updateListener.of(update => {
          if (update.docChanged && !this.updating) {
            this.lastEmittedContent = update.state.doc.toString();
            const content = this.lastEmittedContent;
            this.zone.run(() => this.contentChange.emit(content));
          }
        }),
        EditorView.theme({
          '&': {
            backgroundColor: 'var(--prodguild-surface-raised)',
            color: 'var(--prodguild-text)',
            fontSize: '13px',
            height: '100%',
          },
          '&[data-readonly=true]': { backgroundColor: 'var(--prodguild-surface)' },
          '&.cm-focused': {
            outline: '2px solid var(--prodguild-text-secondary)',
            outlineOffset: '-2px',
          },
          '.cm-scroller': {
            overflow: 'auto',
            minHeight: '0',
            height: '100%',
            fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
            lineHeight: '1.7',
          },
          '.cm-content': { padding: '12px 0' },
          '.cm-line': { padding: '0 16px' },
          '.cm-gutters': {
            backgroundColor: 'var(--prodguild-surface)',
            color: 'var(--prodguild-text-secondary)',
            border: 'none',
            paddingRight: '8px',
          },
          '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--prodguild-bg)' },
          '.cm-panels': {
            backgroundColor: 'var(--prodguild-surface)',
            color: 'var(--prodguild-text)',
            maxWidth: '100%',
          },
          '.cm-search': { display: 'flex', flexWrap: 'wrap', gap: '6px', padding: '12px' },
          '.cm-button': {
            backgroundImage: 'none',
            backgroundColor: 'var(--prodguild-surface-muted)',
            color: 'var(--prodguild-text)',
            border: 'none',
            borderRadius: 'var(--prodguild-radius-sm)',
            padding: '6px 10px',
          },
          '.cm-button:hover, .cm-button:active': {
            backgroundImage: 'none',
            backgroundColor: 'var(--prodguild-hover)',
          },
          '.cm-button:focus-visible': {
            outline: '2px solid var(--prodguild-text)',
            outlineOffset: '2px',
          },
          '.cm-textfield': {
            minWidth: '0',
            maxWidth: '100%',
            backgroundColor: 'var(--prodguild-surface-raised)',
            color: 'var(--prodguild-text)',
            border: '1px solid var(--prodguild-border)',
            borderRadius: 'var(--prodguild-radius-sm)',
            padding: '6px 10px',
          },
        }),
      ],
    });
  }
}
