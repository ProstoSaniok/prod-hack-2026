import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  afterRenderEffect,
  computed,
  effect,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { TuiButton } from '@taiga-ui/core';
import { CodeEditorComponent, EditorLineSelection } from '../code-editor/code-editor.component';
import { FileTreeComponent } from '../file-tree/file-tree.component';
import { BinaryAttachment } from '../solution.models';
import { ProductIconComponent } from '../../../shared/product-icon.component';
import { Extension } from '@codemirror/state';

/** Presentation-only file workspace, independent of accounts, projects and review state. */
export interface EditorDocument {
  readonly id: string;
  readonly path: string;
  readonly content: string;
  readonly readOnly?: boolean;
  readonly modified?: boolean;
  readonly binary?: Pick<BinaryAttachment, 'mimeType' | 'size'>;
}

@Component({
  selector: 'app-repository-editor',
  standalone: true,
  imports: [TuiButton, CodeEditorComponent, FileTreeComponent, ProductIconComponent],
  templateUrl: './repository-editor.component.html',
  styleUrl: './repository-editor.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RepositoryEditorComponent {
  readonly files = input.required<readonly EditorDocument[]>();
  readonly scopeId = input('');
  readonly selectedId = input('');
  readonly content = input('');
  readonly editorExtensions = input<Extension>([]);
  readonly noEditorExtensions: Extension = [];
  readonly readOnly = input(false);
  readonly busy = input(false);
  readonly status = input('');
  readonly showExplorer = input(false);
  readonly responsiveExplorer = input(false);
  readonly allowDelete = input(true);
  readonly showSave = input(true);
  readonly allowCreate = input(true);
  readonly creating = input(false);
  readonly createDisabled = input(false);
  readonly allowUpload = input(false);
  readonly explorerLabel = input('Файлы');
  readonly emptyTitle = input('Ваше решение начинается здесь');
  readonly emptyDescription = input('Выберите файл в проводнике или создайте новый.');
  readonly fileSelect = output<string>();
  readonly contentChange = output<string>();
  readonly selectionEnabled = input(false);
  readonly focusLines = input<{ line: number; end_line: number } | null>(null);
  readonly lineSelection = output<EditorLineSelection | null>();
  readonly save = output<void>();
  readonly remove = output<void>();
  readonly download = output<void>();
  readonly create = output<void>();
  readonly fileCreate = output<string>();
  readonly createCancel = output<void>();
  private readonly createButton = viewChild<ElementRef<HTMLButtonElement>>('createButton');
  private readonly fileTree = viewChild(FileTreeComponent);
  private readonly tabStrip = viewChild<ElementRef<HTMLElement>>('tabStrip');
  readonly upload = output<void>();
  readonly opened = signal<readonly string[]>([]);
  private currentScope = '';
  readonly active = computed(() => this.files().find(file => file.id === this.selectedId()));
  readonly tabs = computed(() =>
    this.opened().flatMap(id => {
      const file = this.files().find(item => item.id === id);
      return file ? [file] : [];
    }),
  );
  readonly breadcrumb = computed(() => this.active()?.path.split('/') ?? []);
  readonly fileType = computed(() => {
    const ext = this.active()?.path.split('.').at(-1)?.toLowerCase() ?? '';
    const names: Readonly<Record<string, string>> = {
      ts: 'TypeScript',
      tsx: 'TypeScript',
      js: 'JavaScript',
      jsx: 'JavaScript',
      py: 'Python',
      sql: 'SQL',
      json: 'JSON',
      md: 'Markdown',
    };
    return names[ext] ?? (this.active()?.binary ? ext.toUpperCase() : 'Обычный текст');
  });
  constructor() {
    afterRenderEffect(onCleanup => {
      this.selectedId();
      this.tabs();
      const strip = this.tabStrip()?.nativeElement;
      if (!strip) return;
      const reveal = () => {
        const active = strip.querySelector<HTMLElement>('.document-tab.active');
        if (!active) return;
        const viewport = strip.getBoundingClientRect();
        const tab = active.getBoundingClientRect();
        if (tab.left < viewport.left) strip.scrollLeft -= viewport.left - tab.left;
        else if (tab.right > viewport.right) strip.scrollLeft += tab.right - viewport.right;
      };
      const observer = new ResizeObserver(reveal);
      observer.observe(strip);
      onCleanup(() => observer.disconnect());
      reveal();
    });
    effect(() => {
      const selected = this.selectedId();
      const files = this.files();
      const scope = this.scopeId();
      untracked(() =>
        this.opened.update(opened => {
          const next =
            scope === this.currentScope
              ? opened.filter(id => files.some(file => file.id === id))
              : [];
          this.currentScope = scope;
          return selected && files.some(file => file.id === selected) && !next.includes(selected)
            ? [...next, selected]
            : next;
        }),
      );
    });
  }
  close(id: string): void {
    if (this.busy()) return;
    const next = this.opened().filter(value => value !== id);
    this.opened.set(next);
    if (id === this.selectedId()) this.fileSelect.emit(next.at(-1) ?? '');
  }
  selectPath(path: string): void {
    const file = this.files().find(item => item.path === path);
    if (file) this.fileSelect.emit(file.id);
  }
  cancelCreate(): void {
    this.createCancel.emit();
    this.createButton()?.nativeElement.focus();
  }
  selectRoot(): void {
    this.fileTree()?.selectRoot();
  }
}
