import { Injectable, signal } from '@angular/core';

import { CaseWork } from '../data/case.models';
import { MOCK_CASES } from '../data/mock-cases';
import {
  ProjectStatus,
  SolutionError,
  SolutionFile,
  SolutionProject,
  SolutionRepository,
  SubmissionSnapshot,
  UploadedFile,
} from './solution.models';

const STORAGE_KEY = 'prodguild.solutions.v1';
const MAX_FILE_LENGTH = 200_000;
const MAX_PROJECT_LENGTH = 4_000_000;

function isAttachment(value: unknown): boolean {
  if (value === undefined) return true;
  if (
    !record(value) ||
    typeof value['base64'] !== 'string' ||
    value['base64'].length > 1_333_336 ||
    typeof value['mimeType'] !== 'string' ||
    typeof value['size'] !== 'number' ||
    !Number.isInteger(value['size']) ||
    value['size'] < 0 ||
    value['size'] > 1_000_000
  )
    return false;
  try {
    return atob(value['base64']).length === value['size'];
  } catch {
    return false;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function validPath(path: string): boolean {
  return (
    path.length <= 240 &&
    !/^[a-z]:/i.test(path) &&
    !path.includes('\\') &&
    ![...path].some(character => character.charCodeAt(0) < 32) &&
    path
      .split('/')
      .every(
        part =>
          !!part.trim() &&
          part === part.trim() &&
          !['.', '..', '.git'].includes(part.toLowerCase()),
      )
  );
}

function isFile(value: unknown): value is SolutionFile {
  return (
    record(value) &&
    typeof value['path'] === 'string' &&
    validPath(value['path']) &&
    typeof value['content'] === 'string' &&
    value['content'].length <= MAX_FILE_LENGTH &&
    typeof value['savedContent'] === 'string' &&
    value['savedContent'].length <= MAX_FILE_LENGTH &&
    typeof value['sha'] === 'string' &&
    isAttachment(value['binary'])
  );
}

function isSnapshot(value: unknown): value is SubmissionSnapshot {
  return (
    record(value) &&
    typeof value['id'] === 'string' &&
    typeof value['commitSha'] === 'string' &&
    typeof value['submittedAt'] === 'string' &&
    Number.isFinite(Date.parse(value['submittedAt'])) &&
    Array.isArray(value['files']) &&
    value['files'].every(
      (file: unknown) =>
        record(file) &&
        typeof file['path'] === 'string' &&
        validPath(file['path']) &&
        typeof file['content'] === 'string' &&
        isAttachment(file['binary']),
    )
  );
}

function isProject(value: unknown): value is SolutionProject {
  if (
    !record(value) ||
    typeof value['id'] !== 'string' ||
    typeof value['ownerId'] !== 'string' ||
    !MOCK_CASES.some(item => item.id === value['caseId']) ||
    !['in_progress', 'submitted', 'completed'].includes(String(value['status'])) ||
    typeof value['commitSha'] !== 'string' ||
    typeof value['updatedAt'] !== 'string' ||
    !Number.isFinite(Date.parse(value['updatedAt'])) ||
    !Array.isArray(value['files']) ||
    !value['files'].every(isFile)
  )
    return false;
  const paths = value['files'].map(file => file.path);
  if (
    new Set(paths).size !== paths.length ||
    paths.some(path => paths.some(other => other !== path && other.startsWith(path + '/')))
  )
    return false;
  if (value['snapshot'] !== null && !isSnapshot(value['snapshot'])) return false;
  if (value['status'] === 'submitted' && value['snapshot'] === null) return false;
  const feedback = value['feedback'];
  return (
    feedback === null ||
    (record(feedback) &&
      feedback['example'] === true &&
      typeof feedback['score'] === 'number' &&
      feedback['score'] >= 0 &&
      feedback['score'] <= 100 &&
      ['approved', 'changes_requested'].includes(String(feedback['verdict'])) &&
      ['strengths', 'problems', 'nextSteps'].every(key => typeof feedback[key] === 'string'))
  );
}

function version(): string {
  return 'mock-' + crypto.randomUUID();
}

/** Browser-only adapter. Version identifiers are mock tokens, not real Git SHAs. */
@Injectable({ providedIn: 'root' })
export class MockSolutionRepository implements SolutionRepository {
  readonly storageNotice = signal('');
  readonly memoryOnly = signal(false);
  private volatile = false;
  private recoveredStorage = false;
  private readonly state = signal<readonly SolutionProject[]>(this.readStorage());
  readonly projects = this.state.asReadonly();

  find(ownerId: string, caseId: string): SolutionProject | undefined {
    return this.projects().find(
      project => project.ownerId === ownerId && project.caseId === caseId,
    );
  }

  async openProject(ownerId: string, caseId: string, legacy?: CaseWork): Promise<SolutionProject> {
    if (!ownerId || !MOCK_CASES.some(item => item.id === caseId))
      throw new SolutionError('not_found', 'Кейс не найден.');
    this.refresh();
    const existing = this.find(ownerId, caseId);
    if (existing) return existing;
    const file: SolutionFile = {
      path: 'solution.md',
      content: legacy?.draft ?? '',
      savedContent: legacy?.draft ?? '',
      sha: version(),
    };
    const status: ProjectStatus = legacy?.status === 'completed' ? 'completed' : 'in_progress';
    const project: SolutionProject = {
      id: crypto.randomUUID(),
      ownerId,
      caseId,
      status,
      commitSha: version(),
      updatedAt: legacy?.updatedAt ?? new Date().toISOString(),
      files: [file],
      snapshot: null,
      feedback:
        ownerId === 'demo' && caseId === 'subscription' && status === 'completed'
          ? {
              example: true,
              score: 84,
              verdict: 'approved',
              strengths: 'Цена и дата списания заметны. Сценарий отмены описан понятно.',
              problems: 'Не показано, что произойдёт при неуспешной оплате.',
              nextSteps: 'Добавьте состояние ошибки оплаты и проверьте тексты на пользователях.',
            }
          : null,
    };
    this.replace(project);
    return project;
  }

  async listFiles(ownerId: string, projectId: string): Promise<readonly SolutionFile[]> {
    this.refresh();
    return this.requireProject(ownerId, projectId).files;
  }

  async readFile(ownerId: string, projectId: string, path: string): Promise<SolutionFile> {
    this.refresh();
    return this.requireFile(this.requireProject(ownerId, projectId), path);
  }

  async createFile(ownerId: string, projectId: string, path: string): Promise<SolutionProject> {
    const project = this.editable(ownerId, projectId);
    this.checkPath(path);
    if (
      project.files.some(
        file =>
          file.path === path ||
          file.path.startsWith(path + '/') ||
          path.startsWith(file.path + '/'),
      )
    )
      throw new SolutionError('validation', 'Этот путь уже занят файлом или папкой.');
    if (project.files.length >= 100)
      throw new SolutionError('validation', 'В демоверсии доступно до 100 файлов на проект.');
    return this.commit({
      ...project,
      files: [...project.files, { path, content: '', savedContent: '', sha: version() }],
    });
  }

  async storeDraft(
    ownerId: string,
    projectId: string,
    path: string,
    content: string,
    expectedSha: string,
  ): Promise<SolutionProject> {
    const project = this.editable(ownerId, projectId);
    if (this.requireFile(project, path).binary)
      throw new SolutionError(
        'validation',
        'Вложения доступны для скачивания, без редактирования.',
      );
    this.checkFile(project, path, content, expectedSha);
    const next = {
      ...project,
      files: project.files.map(file => (file.path === path ? { ...file, content } : file)),
    };
    this.replace(next);
    return next;
  }

  async saveFile(
    ownerId: string,
    projectId: string,
    path: string,
    content: string,
    expectedSha: string,
  ): Promise<SolutionProject> {
    const project = this.editable(ownerId, projectId);
    if (this.requireFile(project, path).binary)
      throw new SolutionError(
        'validation',
        'Вложения доступны для скачивания, без редактирования.',
      );
    this.checkFile(project, path, content, expectedSha);
    const file = this.requireFile(project, path);
    if (content === file.savedContent && content === file.content) return project;
    return this.commit({
      ...project,
      files: project.files.map(item =>
        item.path === path ? { ...item, content, savedContent: content, sha: version() } : item,
      ),
    });
  }

  async deleteFile(
    ownerId: string,
    projectId: string,
    path: string,
    expectedSha: string,
  ): Promise<SolutionProject> {
    const project = this.editable(ownerId, projectId);
    this.checkFile(project, path, this.requireFile(project, path).content, expectedSha);
    return this.commit({ ...project, files: project.files.filter(file => file.path !== path) });
  }

  async submit(
    ownerId: string,
    projectId: string,
    expectedCommitSha: string,
  ): Promise<SolutionProject> {
    this.refresh();
    const project = this.requireProject(ownerId, projectId);
    if (project.snapshot && project.snapshot.commitSha === expectedCommitSha) return project;
    if (project.status !== 'in_progress')
      throw new SolutionError('read_only', 'Решение уже отправлено и доступно только для чтения.');
    if (project.commitSha !== expectedCommitSha)
      throw new SolutionError(
        'conflict',
        'Файлы изменились в другой вкладке. Проверьте актуальную версию перед отправкой.',
      );
    if (!project.files.some(file => file.savedContent.trim() || (file.binary?.size ?? 0) > 0))
      throw new SolutionError(
        'validation',
        'Добавьте решение хотя бы в один файл перед отправкой.',
      );
    if (project.files.some(file => file.content !== file.savedContent))
      throw new SolutionError('validation', 'Сначала сохраните все изменённые файлы.');
    const submittedAt = new Date().toISOString();
    const next: SolutionProject = {
      ...project,
      status: 'submitted',
      updatedAt: submittedAt,
      snapshot: {
        id: crypto.randomUUID(),
        commitSha: project.commitSha,
        submittedAt,
        files: project.files.map(file => ({
          path: file.path,
          content: file.savedContent,
          ...(file.binary ? { binary: file.binary } : {}),
        })),
      },
    };
    this.replace(next);
    return next;
  }

  async getSnapshot(ownerId: string, projectId: string): Promise<SubmissionSnapshot | null> {
    this.refresh();
    return this.requireProject(ownerId, projectId).snapshot;
  }

  async uploadFiles(
    ownerId: string,
    projectId: string,
    files: readonly UploadedFile[],
  ): Promise<SolutionProject> {
    const project = this.editable(ownerId, projectId);
    if (!files.length) return project;
    if (project.files.length + files.length > 100)
      throw new SolutionError('validation', 'В демоверсии доступно до 100 файлов на проект.');
    const paths = [...project.files.map(file => file.path), ...files.map(file => file.path)];
    for (const file of files) {
      this.checkPath(file.path);
      if (file.content.length > MAX_FILE_LENGTH || !isAttachment(file.binary))
        throw new SolutionError(
          'validation',
          'Файл слишком большой: до 200 000 символов текста или 1 МБ для вложения.',
        );
    }
    if (
      new Set(paths).size !== paths.length ||
      paths.some(path => paths.some(other => other !== path && other.startsWith(path + '/')))
    )
      throw new SolutionError(
        'validation',
        'Путь уже занят файлом или папкой. Переименуйте файл перед загрузкой.',
      );
    const total = [...project.files, ...files].reduce(
      (size, file) => size + file.content.length + (file.binary?.base64.length ?? 0),
      0,
    );
    if (total > MAX_PROJECT_LENGTH)
      throw new SolutionError(
        'validation',
        'Достигнут лимит файлов демопроекта. Уменьшите размер загрузки.',
      );
    return this.commit({
      ...project,
      files: [
        ...project.files,
        ...files.map(file => ({ ...file, savedContent: file.content, sha: version() })),
      ],
    });
  }

  private editable(ownerId: string, projectId: string): SolutionProject {
    this.refresh();
    const project = this.requireProject(ownerId, projectId);
    if (project.status !== 'in_progress')
      throw new SolutionError('read_only', 'Это решение доступно только для чтения.');
    return project;
  }

  private requireProject(ownerId: string, projectId: string): SolutionProject {
    const project = this.projects().find(item => item.id === projectId && item.ownerId === ownerId);
    if (!project) throw new SolutionError('not_found', 'Проект не найден.');
    return project;
  }

  private requireFile(project: SolutionProject, path: string): SolutionFile {
    const file = project.files.find(item => item.path === path);
    if (!file) throw new SolutionError('not_found', 'Файл не найден.');
    return file;
  }

  private checkPath(path: string): void {
    if (!validPath(path))
      throw new SolutionError(
        'validation',
        'Укажите относительный путь до 240 символов, например src/main.ts. Пути с .., .git и пустыми частями недопустимы.',
      );
  }

  private checkFile(
    project: SolutionProject,
    path: string,
    content: string,
    expectedSha: string,
  ): void {
    if (this.requireFile(project, path).sha !== expectedSha)
      throw new SolutionError(
        'conflict',
        'Файл изменён в другой вкладке. Ваш текст сохранён на экране: скачайте его или загрузите актуальную версию.',
      );
    if (
      content.length > MAX_FILE_LENGTH ||
      project.files.reduce(
        (size, file) =>
          size +
          (file.path === path
            ? content.length
            : file.content.length + (file.binary?.base64.length ?? 0)),
        0,
      ) > MAX_PROJECT_LENGTH
    )
      throw new SolutionError(
        'validation',
        'Лимит демоверсии: 200 000 символов в текстовом файле и 4 МБ данных в проекте. Скачайте текст, чтобы не потерять изменения.',
      );
  }

  private commit(project: SolutionProject): SolutionProject {
    const next = { ...project, commitSha: version(), updatedAt: new Date().toISOString() };
    this.replace(next);
    return next;
  }

  private replace(project: SolutionProject): void {
    this.state.update(projects => [...projects.filter(item => item.id !== project.id), project]);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.projects()));
      this.volatile = false;
      this.memoryOnly.set(false);
      this.storageNotice.set(
        this.recoveredStorage
          ? 'Хранилище решений было недоступно или повреждено. Использованы прежние записи кейсов. Новые изменения сохраняются в браузере.'
          : '',
      );
    } catch {
      this.volatile = true;
      this.memoryOnly.set(true);
      this.storageNotice.set(
        'Не удалось сохранить файлы в браузере. Они доступны до обновления страницы. Скачайте важные изменения.',
      );
    }
  }

  private refresh(): void {
    if (!this.volatile) {
      const projects = this.readStorage();
      if (!this.volatile) this.state.set(projects);
    }
  }

  private readStorage(): readonly SolutionProject[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const value: unknown = JSON.parse(raw);
      if (
        !Array.isArray(value) ||
        !value.every(isProject) ||
        new Set(value.map(project => project.id)).size !== value.length ||
        new Set(value.map(project => project.ownerId + ':' + project.caseId)).size !== value.length
      )
        throw new Error('Invalid solution storage');
      return value;
    } catch {
      this.volatile = true;
      this.recoveredStorage = true;
      this.memoryOnly.set(true);
      this.storageNotice.set(
        'Хранилище решений недоступно или повреждено. Данные этой сессии могут не сохраниться после обновления.',
      );
      return [];
    }
  }
}
