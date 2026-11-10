import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormControl, FormsModule, ReactiveFormsModule } from '@angular/forms';
import { TuiButton, TuiTextfield } from '@taiga-ui/core';
import { TuiCheckbox } from '@taiga-ui/kit';
import { SplitPaneComponent } from '../../shared/split-pane/split-pane.component';
import { CaseStudy, CaseWork } from '../data/case.models';
import {
  RepositoryEditorComponent,
  EditorDocument,
} from './repository-editor/repository-editor.component';
import {
  RepositoryCloneComponent,
  CloneRepository,
} from './repository-clone/repository-clone.component';
import { readUpload } from './file-upload';
import { FileTreeComponent } from './file-tree/file-tree.component';
import { MockSolutionRepository } from './mock-solution.repository';
import { SolutionError, SolutionProject } from './solution.models';

type Panel = 'conditions' | 'files' | 'review';

@Component({
  selector: 'app-solution-workspace',
  standalone: true,
  imports: [
    DatePipe,
    FormsModule,
    ReactiveFormsModule,
    TuiButton,
    TuiTextfield,
    TuiCheckbox,
    RepositoryEditorComponent,
    RepositoryCloneComponent,
    FileTreeComponent,
    SplitPaneComponent,
  ],
  templateUrl: './solution-workspace.component.html',
  styleUrl: './solution-workspace.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SolutionWorkspaceComponent {
  readonly study = input.required<CaseStudy>();
  readonly ownerId = input.required<string>();
  readonly cloneUrls = input<CloneRepository>();
  readonly legacyWork = input<CaseWork>();
  readonly steps = input<readonly number[]>([]);
  readonly stepChange = output<{ readonly index: number; readonly checked: boolean }>();
  readonly repository = inject(MockSolutionRepository);
  readonly project = signal<SolutionProject | null>(null);
  readonly panel = signal<Panel>('conditions');
  readonly panelExpanded = signal(true);
  readonly compact = signal(false);
  readonly selectedPath = signal('solution.md');
  readonly material = signal(false);
  readonly text = signal('');
  readonly expectedSha = signal('');
  readonly loading = signal(true);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly conflict = signal(false);
  readonly notice = signal('');
  readonly creating = signal(false);
  readonly newPath = new FormControl('', { nonNullable: true });
  readonly confirmation = signal<'submit' | 'delete'>('submit');
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('confirmationDialog');
  private requestId = 0;
  private readonly buffers = signal<
    ReadonlyMap<string, { readonly content: string; readonly sha: string }>
  >(new Map());
  readonly readOnly = computed(() => this.project()?.status !== 'in_progress');
  readonly files = computed(() => {
    const project = this.project();
    return project?.snapshot
      ? project.snapshot.files.map(file => ({
          ...file,
          savedContent: file.content,
          sha: project.snapshot?.commitSha ?? '',
        }))
      : (project?.files ?? []);
  });
  readonly dirty = computed(
    () => this.project()?.files.some(file => file.content !== file.savedContent) ?? false,
  );
  readonly screenOnly = computed(() => {
    const stored = this.repository.find(this.ownerId(), this.study().id);
    return [...this.buffers()].some(
      ([path, buffer]) =>
        stored?.files.find(file => file.path === path)?.content !== buffer.content,
    );
  });
  readonly materials = computed(() => [
    {
      path: 'README.md',
      content: `${this.study().title}\n\n${this.study().context}\n\n${this.study().material}`,
    },
  ]);
  readonly statusLabel = computed(() =>
    this.project()?.status === 'submitted'
      ? 'На проверке'
      : this.project()?.status === 'completed'
        ? 'Кейс завершён'
        : 'В работе',
  );
  readonly cloneRepository = computed<CloneRepository>(
    () =>
      this.cloneUrls() ?? {
        httpsUrl: `https://gitea.prodguild.test/${encodeURIComponent(this.ownerId())}/${this.study().id}.git`,
        sshUrl: `git@gitea.prodguild.test:${encodeURIComponent(this.ownerId())}/${this.study().id}.git`,
        mock: true,
      },
  );
  readonly selectedId = computed(() =>
    this.selectedPath() ? (this.material() ? 'material:' : '') + this.selectedPath() : '',
  );
  readonly documents = computed<readonly EditorDocument[]>(() => [
    ...this.files().map(file => ({
      ...file,
      id: file.path,
      modified: (this.buffers().get(file.path)?.content ?? file.content) !== file.savedContent,
    })),
    ...this.materials().map(file => ({
      ...file,
      id: 'material:' + file.path,
      path: 'Материалы/' + file.path,
      readOnly: true,
    })),
  ]);
  readonly saveStatus = computed(() =>
    this.material() || this.readOnly()
      ? 'Только для чтения'
      : this.screenOnly()
        ? 'Изменения только на экране · скачайте текст перед обновлением'
        : this.repository.memoryOnly()
          ? 'Черновик в текущей сессии'
          : this.dirty()
            ? 'Черновик в браузере · есть изменения без фиксации'
            : 'Версия сохранена в браузере',
  );

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.requestId++;
    });
    effect(() => {
      const study = this.study();
      const ownerId = this.ownerId();
      untracked(() => {
        void this.open(ownerId, study.id);
      });
    });
  }

  selectDocument(id: string): void {
    if (!id) {
      this.selectedPath.set('');
      this.text.set('');
      return;
    }
    this.selectFile(id.startsWith('material:') ? id.slice(9) : id, id.startsWith('material:'));
  }

  navigatePanels(event: KeyboardEvent): void {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const panels: readonly Panel[] = ['conditions', 'files', 'review'];
    const current = panels.indexOf(this.panel());
    const index =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? 2
          : (current + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
    event.preventDefault();
    this.setPanel(panels[index]);
    if (event.currentTarget instanceof HTMLElement)
      event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')[index]?.focus();
  }

  async upload(input: HTMLInputElement): Promise<void> {
    const files = [...(input.files ?? [])];
    if (!files.length) return;
    const owner = this.ownerId();
    const project = this.requireProject();
    const requestId = this.requestId;
    await this.run(async () => {
      if (files.length > 100)
        throw new SolutionError('validation', 'Выберите не более 100 файлов.');
      const uploads = await Promise.all(files.map(readUpload));
      if (requestId !== this.requestId) return;
      const next = await this.repository.uploadFiles(owner, project.id, uploads);
      this.project.set(next);
      this.setPanel('files');
      this.selectFile(uploads[0].path);
      this.notice.set(`Загружено файлов: ${uploads.length}.`);
    });
    input.value = '';
  }

  setPanel(panel: Panel): void {
    this.panel.set(panel);
    this.panelExpanded.set(true);
  }

  selectFile(path: string, material = false): void {
    const file = material
      ? this.materials().find(item => item.path === path)
      : this.files().find(item => item.path === path);
    if (!file) return;
    this.material.set(material);
    this.selectedPath.set(path);
    const buffer = material ? undefined : this.buffers().get(path);
    this.text.set(buffer?.content ?? file.content);
    this.expectedSha.set(buffer?.sha ?? ('sha' in file ? file.sha : ''));
    this.error.set('');
    this.conflict.set(false);
    this.notice.set('');
  }

  async edit(content: string): Promise<void> {
    const project = this.project();
    if (
      !project ||
      this.readOnly() ||
      this.material() ||
      this.busy() ||
      this.files().find(file => file.path === this.selectedPath())?.binary
    )
      return;
    this.text.set(content);
    const path = this.selectedPath();
    const sha = this.expectedSha();
    this.buffers.update(buffers => new Map(buffers).set(path, { content, sha }));

    this.notice.set('');
    const requestId = this.requestId;
    try {
      const next = await this.repository.storeDraft(this.ownerId(), project.id, path, content, sha);
      if (requestId === this.requestId) this.project.set(next);
    } catch (error: unknown) {
      if (requestId === this.requestId) this.fail(error);
    }
  }

  async save(): Promise<void> {
    if (this.material() || this.readOnly()) return;
    await this.run(async () => {
      await this.savePath(this.selectedPath());
      this.notice.set(
        this.repository.memoryOnly()
          ? 'Версия зафиксирована только в текущей сессии.'
          : 'Версия файла сохранена в браузере.',
      );
    });
  }

  async createFile(): Promise<void> {
    await this.run(async () => {
      const next = await this.repository.createFile(
        this.ownerId(),
        this.requireProject().id,
        this.newPath.value.trim(),
      );
      this.project.set(next);
      this.selectFile(this.newPath.value.trim());
      this.newPath.reset();
      this.creating.set(false);
    });
  }

  async requestSubmit(): Promise<void> {
    await this.run(async () => {
      for (const file of this.requireProject().files) {
        const buffer = this.buffers().get(file.path);
        if ((buffer?.content ?? file.content) !== file.savedContent) await this.savePath(file.path);
      }
      if (
        !this.requireProject().files.some(
          file => file.savedContent.trim() || (file.binary?.size ?? 0) > 0,
        )
      )
        throw new SolutionError(
          'validation',
          'Добавьте решение хотя бы в один файл перед отправкой.',
        );
      this.confirmation.set('submit');
      this.dialog().nativeElement.showModal();
    });
  }

  requestDelete(): void {
    this.confirmation.set('delete');
    this.dialog().nativeElement.showModal();
  }
  closeDialog(): void {
    this.dialog().nativeElement.close();
  }

  async confirm(): Promise<void> {
    this.closeDialog();
    await this.run(async () => {
      const project = this.requireProject();
      if (this.confirmation() === 'submit') {
        const next = await this.repository.submit(this.ownerId(), project.id, project.commitSha);
        this.project.set(next);
        this.buffers.set(new Map());

        const path = next.snapshot?.files[0]?.path;
        if (path) this.selectFile(path);
        this.setPanel('review');
        this.notice.set('Решение отправлено на проверку в деморежиме.');
      } else {
        const next = await this.repository.deleteFile(
          this.ownerId(),
          project.id,
          this.selectedPath(),
          this.expectedSha(),
        );
        this.removeBuffer(this.selectedPath());

        this.project.set(next);
        const file = next.files[0];
        if (file) this.selectFile(file.path);
        else {
          this.selectedPath.set('');
          this.text.set('');
        }
        this.notice.set('Файл удалён.');
      }
    });
  }

  async reloadFile(): Promise<void> {
    await this.run(async () => {
      const project = await this.repository.openProject(this.ownerId(), this.study().id);
      this.project.set(project);
      this.removeBuffer(this.selectedPath());

      const path = project.files.some(file => file.path === this.selectedPath())
        ? this.selectedPath()
        : project.files[0]?.path;
      if (path) this.selectFile(path);
      else {
        this.selectedPath.set('');
        this.text.set('');
      }
    });
  }

  download(): void {
    const binary = this.material()
      ? undefined
      : this.files().find(file => file.path === this.selectedPath())?.binary;
    const content = binary
      ? Uint8Array.from(atob(binary.base64), character => character.charCodeAt(0))
      : this.text();
    const url = URL.createObjectURL(
      new Blob([content], {
        type: binary ? 'application/octet-stream' : 'text/plain;charset=utf-8',
      }),
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = this.selectedPath().split('/').at(-1) ?? 'solution.txt';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  private async open(ownerId: string, caseId: string): Promise<void> {
    const requestId = ++this.requestId;
    this.loading.set(true);
    this.error.set('');
    this.project.set(null);
    this.buffers.set(new Map());

    this.material.set(false);
    this.panel.set('conditions');
    this.creating.set(false);
    this.notice.set('');
    try {
      const project = await this.repository.openProject(ownerId, caseId, this.legacyWork());
      if (requestId !== this.requestId) return;
      this.project.set(project);
      if (project.files[0]) this.selectFile(project.files[0].path);
      else {
        this.selectedPath.set('');
        this.text.set('');
      }
    } catch (error: unknown) {
      if (requestId === this.requestId) this.fail(error);
    } finally {
      if (requestId === this.requestId) this.loading.set(false);
    }
  }

  private removeBuffer(path: string): void {
    this.buffers.update(buffers => {
      const next = new Map(buffers);
      next.delete(path);
      return next;
    });
  }

  private requireProject(): SolutionProject {
    const project = this.project();
    if (!project) throw new SolutionError('not_found', 'Проект не найден.');
    return project;
  }

  private async savePath(path: string): Promise<void> {
    const project = this.requireProject();
    const file = project.files.find(item => item.path === path);
    if (!file) throw new SolutionError('not_found', 'Файл не найден.');
    const buffer = this.buffers().get(path);
    const next = await this.repository.saveFile(
      this.ownerId(),
      project.id,
      path,
      buffer?.content ?? file.content,
      buffer?.sha ?? file.sha,
    );
    this.project.set(next);
    this.removeBuffer(path);

    if (this.selectedPath() === path)
      this.expectedSha.set(next.files.find(item => item.path === path)?.sha ?? '');
  }

  private async run(operation: () => Promise<void>): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    this.conflict.set(false);
    this.notice.set('');
    try {
      await operation();
    } catch (error: unknown) {
      this.fail(error);
    } finally {
      this.busy.set(false);
    }
  }

  private fail(error: unknown): void {
    this.error.set(
      error instanceof SolutionError
        ? error.message
        : 'Не удалось выполнить действие. Ваш текст остаётся на экране — скачайте его и повторите попытку.',
    );
    this.conflict.set(
      error instanceof SolutionError && ['conflict', 'not_found', 'read_only'].includes(error.code),
    );
  }
}
