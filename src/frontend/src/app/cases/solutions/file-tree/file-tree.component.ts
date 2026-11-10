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
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { TuiButton, TuiTextfield } from '@taiga-ui/core';
import { ProductIconComponent } from '../../../shared/product-icon.component';

interface TreeNode {
  readonly path: string;
  readonly name: string;
  readonly depth: number;
  readonly directory: boolean;
  readonly draft?: boolean;
}

@Component({
  selector: 'app-file-tree',
  standalone: true,
  imports: [ReactiveFormsModule, TuiButton, TuiTextfield, ProductIconComponent],
  templateUrl: './file-tree.component.html',
  styleUrl: './file-tree.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FileTreeComponent {
  readonly files = input.required<readonly { readonly path: string }[]>();
  readonly selected = input('');
  readonly label = input('Файлы');
  readonly creating = input(false);
  readonly createDisabled = input(false);
  readonly fileSelect = output<string>();
  readonly fileCreate = output<string>();
  readonly createCancel = output<void>();
  readonly collapsed = signal<ReadonlySet<string>>(new Set());
  readonly filename = new FormControl('', { nonNullable: true });
  readonly filenameInput = viewChild<ElementRef<HTMLInputElement>>('filenameInput');
  readonly inputId = `new-file-${crypto.randomUUID()}`;
  readonly creationParent = signal('');
  readonly validationError = signal('');
  private readonly selectedDirectory = signal<string | null>(null);
  readonly nodes = computed(() => {
    const result = new Map<string, TreeNode>();
    for (const file of this.files()) {
      const parts = file.path.split('/');
      parts.forEach((name, depth) => {
        const path = parts.slice(0, depth + 1).join('/');
        result.set(path, { path, name, depth, directory: depth < parts.length - 1 });
      });
    }
    const nodes = [...result.values()]
      .sort((a, b) => {
        const left = a.path.split('/');
        const right = b.path.split('/');
        for (let index = 0; index < Math.min(left.length, right.length); index++) {
          if (left[index] !== right[index]) {
            const leftFolder = index < left.length - 1 || a.directory;
            const rightFolder = index < right.length - 1 || b.directory;
            return leftFolder !== rightFolder
              ? leftFolder
                ? -1
                : 1
              : left[index].localeCompare(right[index], 'ru');
          }
        }
        return left.length - right.length;
      })
      .filter(node => ![...this.collapsed()].some(path => node.path.startsWith(path + '/')));
    if (this.creating()) {
      const parent = this.creationParent();
      nodes.splice(parent ? nodes.findIndex(node => node.path === parent) + 1 : 0, 0, {
        path: '\0new-file',
        name: '',
        depth: parent ? parent.split('/').length : 0,
        directory: false,
        draft: true,
      });
    }
    return nodes;
  });

  constructor() {
    effect(() => {
      const selected = this.selected();
      this.selectedDirectory.set(selected ? selected.split('/').slice(0, -1).join('/') : null);
    });
    effect(() => {
      if (this.creating()) untracked(() => this.prepareCreation());
    });
    afterRenderEffect(() => {
      const input = this.filenameInput()?.nativeElement;
      if (input) input.focus();
    });
  }

  selectRoot(): void {
    if (this.createDisabled()) return;
    this.selectedDirectory.set('');
    if (this.creating()) this.creationParent.set('');
  }

  private prepareCreation(): void {
    const requested = this.selectedDirectory() ?? this.selected().split('/').slice(0, -1).join('/');
    const parent = this.files().some(file => file.path.startsWith(requested + '/'))
      ? requested
      : '';
    this.creationParent.set(parent);
    this.filename.reset();
    this.validationError.set('');
    this.collapsed.update(
      previous =>
        new Set([...previous].filter(path => parent !== path && !parent.startsWith(path + '/'))),
    );
  }

  submit(event?: Event): void {
    event?.preventDefault();
    if (this.createDisabled()) return;
    const name = this.filename.value.trim();
    const path = [this.creationParent(), name].filter(Boolean).join('/');
    let error = '';
    if (!name) error = 'Введите имя файла.';
    else if (new TextEncoder().encode(path).length > 1024) error = 'Путь к файлу слишком длинный.';
    else if (
      path.includes('\\') ||
      [...path].some(
        character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
      ) ||
      path
        .split('/')
        .some(part => !part || part === '.' || part === '..' || part.toLowerCase() === '.git')
    )
      error = 'Используйте путь через / без пустых частей, «.», «..» и «.git».';
    else if (this.files().some(file => file.path === path))
      error = 'Файл с таким именем уже существует.';
    else if (
      this.files().some(
        file => file.path.startsWith(path + '/') || path.startsWith(file.path + '/'),
      )
    )
      error = 'Это имя уже занято файлом или папкой.';
    this.validationError.set(error);
    if (error) this.filenameInput()?.nativeElement.focus();
    else this.fileCreate.emit(path);
  }

  cancel(event?: Event): void {
    event?.preventDefault();
    event?.stopPropagation();
    if (!this.createDisabled()) this.createCancel.emit();
  }

  select(node: TreeNode): void {
    if (!node.directory) {
      this.selectedDirectory.set(node.path.split('/').slice(0, -1).join('/'));
      this.fileSelect.emit(node.path);
      return;
    }
    this.selectedDirectory.set(node.path);
    if (
      this.creating() &&
      (this.creationParent() === node.path || this.creationParent().startsWith(node.path + '/'))
    )
      return;
    this.collapsed.update(previous => {
      const next = new Set(previous);
      if (next.has(node.path)) next.delete(node.path);
      else next.add(node.path);
      return next;
    });
  }
}
