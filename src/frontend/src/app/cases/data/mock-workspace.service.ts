import { computed, effect, inject, Injectable, signal, untracked } from '@angular/core';

import { CaseWork, MockAccount, User } from './case.models';
import { MOCK_CASES } from './mock-cases';
import { MockSolutionRepository } from '../solutions/mock-solution.repository';
import { BackendService } from '../../api/backend.service';

const ACCOUNTS_KEY = 'lotty.mock.accounts.v1';
const SESSION_KEY = 'lotty.mock.session.v1';
const WORK_KEY = 'lotty.mock.work.v1.';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isAccount(value: unknown): value is MockAccount {
  return (
    isRecord(value) &&
    typeof value['login'] === 'string' &&
    typeof value['name'] === 'string' &&
    typeof value['salt'] === 'string' &&
    typeof value['passwordHash'] === 'string'
  );
}

function isWork(value: unknown): value is CaseWork {
  return (
    isRecord(value) &&
    typeof value['caseId'] === 'string' &&
    MOCK_CASES.some(item => item.id === value['caseId']) &&
    (value['status'] === 'started' || value['status'] === 'completed') &&
    typeof value['draft'] === 'string' &&
    value['draft'].length <= 20000 &&
    Array.isArray(value['steps']) &&
    value['steps'].every(
      (step: unknown) =>
        typeof step === 'number' && Number.isInteger(step) && step >= 0 && step < 3,
    ) &&
    typeof value['updatedAt'] === 'string' &&
    Number.isFinite(Date.parse(value['updatedAt']))
  );
}

/** Frontend-only mock boundary. Replace this service with an API adapter for production. */
@Injectable({ providedIn: 'root' })
export class MockWorkspaceService {
  readonly api = inject(BackendService);
  private readonly solutions = inject(MockSolutionRepository);
  readonly cases = MOCK_CASES;
  readonly storageNotice = signal('');
  private readonly accounts = signal<readonly MockAccount[]>(this.readAccounts());
  private readonly sessionWork = new Map<string, readonly CaseWork[]>();
  private readonly currentUser = signal<User | null>(this.restoreSession());
  readonly user = computed(() => {
    const user = this.api.user();
    return user
      ? { name: user.name, login: user.id }
      : this.currentUser()?.login === 'demo'
        ? this.currentUser()
        : null;
  });
  readonly creditsBalance = computed<number | null>(() =>
    this.api.user()
      ? (this.api.wallet()?.available ?? null)
      : this.user()?.login === 'demo'
        ? 1200
        : null,
  );
  private readonly currentWork = signal<readonly CaseWork[]>(this.readWork());
  readonly works = computed(() =>
    this.currentWork().map(work => {
      const project = this.solutions.find(this.user()?.login ?? '', work.caseId);
      return project
        ? {
            ...work,
            status: project.status === 'in_progress' ? ('started' as const) : project.status,
          }
        : work;
    }),
  );
  readonly started = computed(() => this.works().filter(work => work.status !== 'completed'));

  constructor() {
    effect(() => {
      this.user();
      untracked(() => this.currentWork.set(this.readWork()));
    });
  }

  async register(name: string, login: string, password: string): Promise<string | null> {
    const normalized = login.trim().toLowerCase();
    if (normalized === 'demo' || this.accounts().some(account => account.login === normalized)) {
      return 'Этот логин уже занят. Выберите другой или войдите.';
    }
    const salt = crypto.randomUUID();
    const account: MockAccount = {
      name: name.trim(),
      login: normalized,
      salt,
      passwordHash: await this.hash(password, salt),
    };
    this.accounts.update(accounts => [...accounts, account]);
    this.write(ACCOUNTS_KEY, this.accounts());
    this.openSession(account);
    return null;
  }

  async login(login: string, password: string): Promise<string | null> {
    const account = this.accounts().find(item => item.login === login.trim().toLowerCase());
    if (!account || (await this.hash(password, account.salt)) !== account.passwordHash) {
      return 'Неверный логин или пароль. Проверьте данные и попробуйте снова.';
    }
    this.openSession(account);
    return null;
  }

  demo(): void {
    this.openSession({ name: 'Анна Смирнова', login: 'demo' });
    if (this.works().length) return;
    const updatedAt = new Date().toISOString();
    this.currentWork.set([
      {
        caseId: 'customer-retention',
        status: 'started',
        draft:
          'Основная метрика — удержание на 30-й день. В первую очередь проверю переход от первого входа к первому переводу.',
        steps: [0],
        updatedAt,
      },
      { caseId: 'money-transfer', status: 'started', draft: '', steps: [], updatedAt },
      {
        caseId: 'subscription',
        status: 'completed',
        draft:
          'На экране подписки показываем цену 299 ₽ в месяц и дату первого списания. Отмена доступна из настроек. После отмены показываем дату окончания доступа и отправляем подтверждение.',
        steps: [0, 1, 2],
        updatedAt,
      },
    ]);
    this.persistWork();
  }

  logout(): void {
    this.currentUser.set(null);
    this.currentWork.set([]);
    this.write(SESSION_KEY, null);
  }

  workFor(caseId: string): CaseWork | undefined {
    return this.works().find(work => work.caseId === caseId);
  }

  start(caseId: string): void {
    if (this.api.isOrganizer()) return;
    if (!this.user() || this.workFor(caseId) || !this.cases.some(item => item.id === caseId))
      return;
    this.currentWork.update(works => [
      ...works,
      {
        caseId,
        status: 'started',
        draft: '',
        steps: [],
        updatedAt: new Date().toISOString(),
      },
    ]);
    this.persistWork();
  }

  save(caseId: string, draft: string, steps: readonly number[]): void {
    if (this.api.isOrganizer()) return;
    const work = this.workFor(caseId);
    if (!this.user() || !work || work.status !== 'started' || draft.length > 20000) return;
    const validSteps = [...new Set(steps)].filter(
      step => Number.isInteger(step) && step >= 0 && step < 3,
    );
    this.currentWork.update(works =>
      works.map(item =>
        item.caseId === caseId
          ? { ...item, draft, steps: validSteps, updatedAt: new Date().toISOString() }
          : item,
      ),
    );
    this.persistWork();
  }

  private openSession(user: User): void {
    this.currentUser.set({ name: user.name, login: user.login });
    this.write(SESSION_KEY, user.login);
    this.currentWork.set(this.readWork());
  }

  private restoreSession(): User | null {
    const login = this.read(SESSION_KEY);
    if (login === 'demo') return { login, name: 'Анна Смирнова' };
    const account = this.accounts().find(item => item.login === login);
    return account ? { name: account.name, login: account.login } : null;
  }

  private readAccounts(): readonly MockAccount[] {
    const value = this.read(ACCOUNTS_KEY);
    return Array.isArray(value) ? value.filter(isAccount) : [];
  }

  private readWork(): readonly CaseWork[] {
    const login = this.user()?.login;
    if (!login) return [];
    const cached = this.sessionWork.get(login);
    if (cached) return cached;
    const value = this.read(WORK_KEY + login);
    return Array.isArray(value)
      ? value.filter(isWork).map(work => ({ ...work, steps: [...new Set(work.steps)] }))
      : [];
  }

  private persistWork(): void {
    const login = this.user()?.login;
    if (login) {
      this.sessionWork.set(login, this.currentWork());
      this.write(WORK_KEY + login, this.currentWork());
    }
  }

  private read(key: string): unknown {
    try {
      const value = localStorage.getItem(key);
      return value ? JSON.parse(value) : null;
    } catch {
      this.storageNotice.set(
        'Хранилище браузера недоступно или повреждено. Данные этой сессии могут не сохраниться после обновления.',
      );
      return null;
    }
  }

  private write(key: string, value: unknown): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      this.storageNotice.set(
        'Не удалось сохранить данные в браузере. Они доступны до обновления страницы.',
      );
    }
  }

  private async hash(password: string, salt: string): Promise<string> {
    const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(salt + password));
    return Array.from(new Uint8Array(buffer), byte => byte.toString(16).padStart(2, '0')).join('');
  }
}
