import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { computed, DestroyRef, inject, Injectable, InjectionToken, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { firstValueFrom, timeout } from 'rxjs';

export const API_ORIGIN = new InjectionToken<string>('API_ORIGIN', { factory: () => '' });
const SESSION_KEY = 'prodguild.api.session.v1';
const ADMIN_SESSION_KEY = 'prodguild.admin.session.v1';

export interface ApiUser {
  id: string;
  role: 'user' | 'admin';
  email: string;
  name: string;
  capabilities: readonly string[];
}
export interface Wallet {
  settled: number;
  reserved: number;
  available: number;
  unit: string;
}
export interface Competency {
  id: string;
  name: string;
  description: string;
}
export interface UserSkill {
  competency_id: string;
  name: string;
  grade: 'performer' | 'review_minus' | 'review_plus' | 'expert';
  displayed_value: number;
}
export type SkillGrade = UserSkill['grade'];
export interface SkillProbation {
  id: string;
  competency_id: string;
  grade: SkillGrade;
  confirmed_reviews: number;
  remaining: number;
  required_successes: number;
  passed: boolean;
}
export interface ProfileSkill {
  competency_id: string;
  name: string;
  grade: SkillGrade;
  value: number;
  contribution_sum: number;
  version: number;
  grant: 'none' | 'supervised' | 'confirmed' | 'revoked';
  review_allowed: boolean;
  expert_confirmed: boolean;
  probation: SkillProbation | null;
  expert_application: { can_apply: boolean; pending_application_id: string | null } | null;
  next_grade?: {
    grade: SkillGrade;
    xp_remaining: number;
    successful_cases: number;
    previous_probation_passed: boolean;
  } | null;
}
export interface UserProfile {
  id: string;
  display_name: string;
  grade: SkillGrade;
  competencies: ProfileSkill[];
  wallet: Wallet;
  karma: number;
  prodits: number;
}
export interface GradeGate {
  grade: SkillGrade;
  minimum_value: number;
  successful_cases: number;
  confirmed_good_reviews: number;
  previous_probation_required: boolean;
  independent_readiness_required: boolean;
}
export interface DevelopmentPolicy {
  version: string;
  progression_version: string;
  state: 'configured';
  formula: string;
  success_score: number;
  quality_score: number;
  probation_required_reviews: number;
  display_cap: number;
  grade_gates: GradeGate[];
  priority_cost: number | null;
}
export interface Activity {
  id: string;
  type: string;
  resource_id: string;
  description: string;
  created_at: string;
}
export interface Transaction {
  id: string;
  sequence: number;
  kind: string;
  source_id?: string;
  settled_delta: number;
  reserved_delta: number;
  created_at: string;
}
export interface Readiness {
  ok: boolean;
  checks: { database: boolean; repository_storage: boolean };
}
function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Сервер вернул некорректные данные.');
  }
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Сервер вернул некорректные данные.');
  return value;
}
function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new Error('Сервер вернул некорректные данные.');
  }
  return value;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error('Сервер вернул некорректные данные.');
  return value;
}
function flag(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Сервер вернул некорректные данные.');
  return value;
}
function decimal(value: unknown, maximum = Number.MAX_VALUE): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > maximum)
    throw new Error('Сервер вернул некорректное значение навыка.');
  return value;
}
function skillGrade(value: unknown): SkillGrade {
  if (
    value !== 'performer' &&
    value !== 'review_minus' &&
    value !== 'review_plus' &&
    value !== 'expert'
  )
    throw new Error('Сервер вернул неизвестный грэйд.');
  return value;
}
function walletValue(value: unknown): Wallet {
  const data = record(value);
  const wallet = {
    settled: number(data['settled']),
    reserved: number(data['reserved']),
    available: number(data['available']),
    unit: text(data['unit']),
  };
  if (
    wallet.settled < 0 ||
    wallet.reserved < 0 ||
    wallet.available < 0 ||
    wallet.settled - wallet.reserved !== wallet.available
  )
    throw new Error('Сервер вернул некорректный баланс.');
  return wallet;
}
function probationValue(value: unknown): SkillProbation | null {
  if (value === null) return null;
  const data = record(value);
  return {
    id: text(data['id']),
    competency_id: text(data['competency_id']),
    grade: skillGrade(data['grade']),
    confirmed_reviews: number(data['confirmed_reviews']),
    remaining: number(data['remaining']),
    required_successes: number(data['required_successes']),
    passed: flag(data['passed']),
  };
}

@Injectable({ providedIn: 'root' })
export class BackendService {
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  readonly origin = inject(API_ORIGIN);
  readonly user = signal<ApiUser | null>(null);
  readonly adminUserId = signal('');
  readonly isOrganizer = computed(
    () => this.user()?.role === 'admin' || this.user()?.capabilities.includes('organizer') === true,
  );
  readonly canReview = signal(false);
  readonly canAssessReadiness = signal(false);
  // The master API exposes confirmed expert rights through can_assess_readiness.
  // This enables the expert view; the server still chooses from one shared queue.
  readonly canQualityReview = computed(() => this.canAssessReadiness());
  readonly hasOrdinaryAssignments = signal(false);
  readonly hasQualityAssignments = signal(false);
  readonly hasReviewAssignments = computed(
    () => this.hasOrdinaryAssignments() || this.hasQualityAssignments(),
  );
  readonly canOpenReviews = computed(() => this.canReview() || this.hasOrdinaryAssignments());
  readonly canOpenQualityReviews = computed(
    () => this.canQualityReview() || this.hasQualityAssignments(),
  );
  readonly sessionScope = signal('');
  readonly reviewAccessError = signal('');
  readonly wallet = signal<Wallet | null>(null);
  readonly notice = signal('');
  private token = '';
  private adminToken = '';
  private adminInitialization: Promise<void> | null = null;
  private email = '';
  private name = '';
  private epoch = 0;
  private reviewAccessRequest: { epoch: number; promise: Promise<void> } | null = null;
  readonly initialized = this.restore();

  private headers(): HttpHeaders {
    return new HttpHeaders(this.token ? { Authorization: `Bearer ${this.token}` } : {});
  }

  private async get(path: string): Promise<unknown> {
    return this.request('GET', path);
  }

  async request(
    method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
    path: string,
    body?: unknown,
    key?: string,
    timeoutMs = 10000,
  ): Promise<unknown> {
    const epoch = this.epoch;
    const adminRequest =
      !!this.adminToken &&
      (path.startsWith('/api/v1/admin/') || path.startsWith('/api/v1/competencies'));
    try {
      return await firstValueFrom(
        this.http
          .request<unknown>(method, this.origin + path, {
            body,
            headers: key
              ? this.requestHeaders(path).set('Idempotency-Key', key)
              : this.requestHeaders(path),
          })
          .pipe(timeout(timeoutMs)),
      );
    } catch (error) {
      if (epoch === this.epoch && error instanceof HttpErrorResponse && error.status === 401) {
        if (adminRequest) {
          this.adminToken = '';
          this.adminUserId.set('');
        } else this.clear();
      }
      throw error;
    }
  }

  private requestHeaders(path: string): HttpHeaders {
    return (path.startsWith('/api/v1/admin/') || path.startsWith('/api/v1/competencies')) &&
      this.adminToken
      ? new HttpHeaders({ Authorization: `Bearer ${this.adminToken}` })
      : this.headers();
  }

  async download(path: string): Promise<Blob> {
    if (!path.startsWith('/api/v1/') || /[\\\r\n]/.test(path))
      throw new Error('Некорректный адрес файла.');
    const epoch = this.epoch;
    try {
      return await firstValueFrom(
        this.http
          .get(this.origin + path, {
            headers: this.requestHeaders(path),
            responseType: 'blob',
          })
          .pipe(timeout(60_000)),
      );
    } catch (error) {
      if (epoch === this.epoch && error instanceof HttpErrorResponse && error.status === 401)
        this.clear();
      throw error;
    }
  }

  ensureAdminAccess(): Promise<void> {
    this.adminInitialization ??= this.initializeAdmin().finally(
      () => (this.adminInitialization = null),
    );
    return this.adminInitialization;
  }

  private async initializeAdmin(): Promise<void> {
    try {
      this.adminToken ||= sessionStorage.getItem(ADMIN_SESSION_KEY) ?? '';
    } catch {
      /* Memory-only admin session. */
    }
    if (this.adminToken) {
      try {
        const value = record(
          await firstValueFrom(
            this.http
              .get<unknown>(this.origin + '/auth/me', {
                headers: new HttpHeaders({ Authorization: `Bearer ${this.adminToken}` }),
              })
              .pipe(timeout(10000)),
          ),
        );
        if (list(value['capabilities']).map(text).includes('organizer')) {
          this.adminUserId.set(text(value['id']));
          return;
        }
      } catch {
        this.adminToken = '';
      }
    }
    this.adminUserId.set('');
    const result = record(
      await firstValueFrom(
        this.http
          .post<unknown>(this.origin + '/auth/sign_in/email', {
            email: 'organizer.local@example.com',
            password: 'Local-demo-2026!',
          })
          .pipe(timeout(10000)),
      ),
    );
    if (result['token_type'] !== 'bearer') throw new Error('Некорректная авторизация админки.');
    this.adminToken = text(result['access_token']);
    const identity = record(
      await firstValueFrom(
        this.http
          .get<unknown>(this.origin + '/auth/me', {
            headers: new HttpHeaders({ Authorization: `Bearer ${this.adminToken}` }),
          })
          .pipe(timeout(10000)),
      ),
    );
    this.adminUserId.set(text(identity['id']));
    try {
      sessionStorage.setItem(ADMIN_SESSION_KEY, this.adminToken);
    } catch {
      /* Memory-only admin session. */
    }
  }

  async authenticate(
    email: string,
    password: string,
    registration: boolean,
    name = '',
  ): Promise<void> {
    await this.initialized;
    const result = record(
      await firstValueFrom(
        this.http
          .post<unknown>(
            this.origin + (registration ? '/auth/sign_up/email' : '/auth/sign_in/email'),
            { email: email.trim(), password },
          )
          .pipe(timeout(10000)),
      ),
    );
    if (result['token_type'] !== 'bearer') throw new Error('Неизвестный тип сессии.');
    this.token = text(result['access_token']);
    this.sessionScope.set(crypto.randomUUID());
    this.epoch++;
    // A new session must never display the previous account's identity or balance.
    this.user.set(null);
    this.clearReviewAccess();
    this.wallet.set(null);
    this.email = email.trim();
    this.name = name.trim() || this.email;
    this.persist();
    await this.currentUser();
    await this.refreshWallet();
  }

  private async restore(): Promise<void> {
    try {
      const stored = sessionStorage.getItem(SESSION_KEY);
      if (!stored) return;
      const value = record(JSON.parse(stored));
      this.token = text(value['token']);
      this.email = text(value['email']);
      this.name = text(value['name']);
      this.sessionScope.set(
        typeof value['session_id'] === 'string' ? value['session_id'] : crypto.randomUUID(),
      );
      this.persist();
    } catch {
      this.clear();
      return;
    }
    try {
      await this.currentUser();
      await this.refreshWallet();
    } catch (error) {
      this.notice.set(this.errorMessage(error));
    }
  }

  async currentUser(): Promise<void> {
    const epoch = this.epoch;
    const value = record(await this.get('/auth/me'));
    if (epoch !== this.epoch) return;
    const role = value['role'];
    if (role !== 'user' && role !== 'admin') throw new Error('Неизвестная роль пользователя.');
    const capabilities = list(value['capabilities']).map(text);
    this.user.set({
      id: text(value['id']),
      role,
      email: this.email,
      name: this.name,
      capabilities,
    });
    await this.refreshReviewAccess();
  }

  applyReviewAccess(value: unknown): void {
    const mode = record(value);
    const canReview = flag(mode['can_review']);
    const canAssessReadiness =
      mode['can_assess_readiness'] === undefined ? false : flag(mode['can_assess_readiness']);
    const tasks = (mode['tasks'] === undefined ? [] : list(mode['tasks'])).map(value => {
      const task = record(value);
      const kind = text(task['kind']);
      if (kind !== 'review' && kind !== 'quality_check')
        throw new Error('Сервер вернул неизвестный вид назначения.');
      return { kind, status: text(task['status']) };
    });
    this.canReview.set(canReview);
    this.canAssessReadiness.set(canAssessReadiness);
    this.hasOrdinaryAssignments.set(
      tasks.some(task => task.kind === 'review' && task.status === 'assigned'),
    );
    this.hasQualityAssignments.set(
      tasks.some(task => task.kind === 'quality_check' && task.status === 'assigned'),
    );
    this.reviewAccessError.set('');
  }

  private clearReviewAccess(): void {
    this.canReview.set(false);
    this.canAssessReadiness.set(false);
    this.hasOrdinaryAssignments.set(false);
    this.hasQualityAssignments.set(false);
    this.reviewAccessError.set('');
  }

  async refreshReviewAccess(): Promise<void> {
    if (!this.user()) return;
    const epoch = this.epoch;
    if (this.reviewAccessRequest?.epoch === epoch) return this.reviewAccessRequest.promise;
    const promise = (async () => {
      try {
        const mode = await this.get('/api/v1/me/review-mode');
        if (epoch === this.epoch) this.applyReviewAccess(mode);
      } catch {
        if (epoch === this.epoch) {
          this.clearReviewAccess();
          this.reviewAccessError.set('Не удалось проверить доступ к ревью.');
        }
      } finally {
        if (this.reviewAccessRequest?.epoch === epoch) this.reviewAccessRequest = null;
      }
    })();
    this.reviewAccessRequest = { epoch, promise };
    return promise;
  }

  async refreshWallet(): Promise<boolean> {
    const epoch = this.epoch;
    try {
      const value = record(await this.get('/api/v1/me/wallet'));
      if (epoch !== this.epoch) return false;
      this.wallet.set({
        settled: number(value['settled']),
        reserved: number(value['reserved']),
        available: number(value['available']),
        unit: text(value['unit']),
      });
      this.notice.set('');
      return true;
    } catch (error) {
      if (epoch === this.epoch) {
        this.notice.set(this.errorMessage(error));
      }
      return false;
    }
  }

  async competencies(offset = 0): Promise<Competency[]> {
    return list(await this.get(`/api/v1/competencies?limit=20&offset=${offset}`)).map(item => {
      const value = record(item);
      return {
        id: text(value['id']),
        name: text(value['name']),
        description: text(value['description']),
      };
    });
  }

  async transactions(offset = 0): Promise<Transaction[]> {
    return list(await this.get(`/api/v1/me/wallet/transactions?limit=20&offset=${offset}`)).map(
      item => {
        const value = record(item);
        return {
          id: text(value['id']),
          sequence: number(value['sequence']),
          kind: text(value['kind']),
          source_id:
            value['source_id'] === null || value['source_id'] === undefined
              ? undefined
              : text(value['source_id']),
          settled_delta: number(value['settled_delta']),
          reserved_delta: number(value['reserved_delta']),
          created_at: text(value['created_at']),
        };
      },
    );
  }

  async profile(): Promise<UserProfile> {
    const epoch = this.epoch;
    const value = record(await this.get('/api/v1/me/profile'));
    const profile: UserProfile = {
      id: text(value['id']),
      display_name: text(value['display_name']),
      grade: skillGrade(value['grade']),
      competencies: list(value['competencies']).map(item => {
        const skill = record(item);
        const grant = skill['grant'];
        if (
          grant !== 'none' &&
          grant !== 'supervised' &&
          grant !== 'confirmed' &&
          grant !== 'revoked'
        )
          throw new Error('Сервер вернул некорректные данные навыка.');
        return {
          competency_id: text(skill['competency_id']),
          name: text(skill['name']),
          grade: skillGrade(skill['grade']),
          value: decimal(skill['value'], 100),
          contribution_sum: decimal(skill['contribution_sum']),
          version: number(skill['version']),
          grant,
          review_allowed: flag(skill['review_allowed']),
          expert_confirmed: flag(skill['expert_confirmed']),
          probation: probationValue(skill['probation']),
          next_grade:
            skill['next_grade'] === undefined || skill['next_grade'] === null
              ? null
              : (() => {
                  const progress = record(skill['next_grade']);
                  return {
                    grade: skillGrade(progress['grade']),
                    xp_remaining: decimal(progress['xp_remaining']),
                    successful_cases: number(progress['successful_cases']),
                    previous_probation_passed: flag(progress['previous_probation_passed']),
                  };
                })(),
          expert_application:
            skill['expert_application'] === undefined || skill['expert_application'] === null
              ? null
              : (() => {
                  const application = record(skill['expert_application']);
                  return {
                    can_apply: flag(application['can_apply']),
                    pending_application_id:
                      application['pending_application_id'] === null
                        ? null
                        : text(application['pending_application_id']),
                  };
                })(),
        };
      }),
      wallet: walletValue(value['wallet']),
      karma: number(value['karma']),
      prodits: number(value['prodits']),
    };
    if (epoch === this.epoch && this.user()?.id === profile.id) this.wallet.set(profile.wallet);
    return profile;
  }

  async developmentPolicy(): Promise<DevelopmentPolicy> {
    const value = record(await this.get('/api/v1/development/policy'));
    if (value['state'] !== 'configured') throw new Error('Правила развития недоступны.');
    const gates = list(value['grade_gates']).map(item => {
      const gate = record(item);
      return {
        grade: skillGrade(gate['grade']),
        minimum_value: decimal(gate['minimum_value'], 100),
        successful_cases: number(gate['successful_cases']),
        confirmed_good_reviews: number(gate['confirmed_good_reviews']),
        previous_probation_required: flag(gate['previous_probation_required']),
        independent_readiness_required: flag(gate['independent_readiness_required']),
      };
    });
    if (gates.length !== 4 || new Set(gates.map(gate => gate.grade)).size !== 4)
      throw new Error('Сервер вернул неполные правила развития.');
    return {
      version: text(value['version']),
      progression_version: text(value['progression_version']),
      state: 'configured',
      formula: text(value['formula']),
      success_score: decimal(value['success_score'], 100),
      quality_score: decimal(value['quality_score'], 100),
      probation_required_reviews: number(value['probation_required_reviews']),
      display_cap: decimal(value['display_cap'], 100),
      grade_gates: gates,
      priority_cost: value['priority_cost'] === undefined ? null : number(value['priority_cost']),
    };
  }

  async activity(offset = 0): Promise<Activity[]> {
    return list(await this.get(`/api/v1/me/history?limit=20&offset=${offset}`)).map(item => {
      const value = record(item);
      return {
        id: text(value['id']),
        type: text(value['type']),
        resource_id: text(value['resource_id']),
        description: text(value['description']),
        created_at: text(value['created_at']),
      };
    });
  }

  async skills(offset = 0): Promise<UserSkill[]> {
    return list(await this.get(`/api/v1/me/skills?limit=20&offset=${offset}`)).map(item => {
      const value = record(item);
      const grade = value['grade'];
      const decimal = text(value['displayed_value']);
      const level = Number(decimal);
      if (
        (grade !== 'performer' &&
          grade !== 'review_minus' &&
          grade !== 'review_plus' &&
          grade !== 'expert') ||
        !/^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(decimal) ||
        !Number.isFinite(level) ||
        level < 0 ||
        level > 100
      ) {
        throw new Error('Сервер вернул некорректные данные навыка.');
      }
      return {
        competency_id: text(value['competency_id']),
        name: text(value['name']),
        grade,
        displayed_value: level,
      };
    });
  }

  async health(): Promise<boolean> {
    return record(await this.get('/healthcheck'))['ok'] === true;
  }

  async readiness(): Promise<Readiness> {
    let response: unknown;
    try {
      response = await this.get('/ready');
    } catch (error) {
      if (!(error instanceof HttpErrorResponse) || error.status !== 503) throw error;
      response = error.error;
    }
    const value = record(response);
    const checks = record(value['checks']);
    if (
      typeof value['ok'] !== 'boolean' ||
      typeof checks['database'] !== 'boolean' ||
      typeof checks['repository_storage'] !== 'boolean'
    )
      throw new Error('Некорректный ответ readiness.');
    return {
      ok: value['ok'],
      checks: { database: checks['database'], repository_storage: checks['repository_storage'] },
    };
  }

  async logout(): Promise<void> {
    try {
      await firstValueFrom(
        this.http
          .post(this.origin + '/auth/sign_out', null, { headers: this.headers() })
          .pipe(timeout(10000)),
      );
    } catch (error) {
      if (!(error instanceof HttpErrorResponse) || error.status !== 401) throw error;
    }
    this.clear();
  }

  startLocalDemo(): void {
    const headers = this.headers();
    const authenticated = !!this.token;
    this.clear();
    this.notice.set('');
    const epoch = this.epoch;
    if (authenticated) {
      this.http
        .post(this.origin + '/auth/sign_out', null, { headers })
        .pipe(timeout(3000), takeUntilDestroyed(this.destroyRef))
        .subscribe({
          error: () => {
            if (epoch === this.epoch)
              this.notice.set(
                'Демо работает локально. Сервер не подтвердил завершение предыдущей сессии.',
              );
          },
        });
    }
  }

  errorMessage(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 401)
        return error.url?.includes('/auth/sign_in/')
          ? 'Неверный email или пароль.'
          : 'Сессия истекла. Войдите снова.';
      if (error.status === 409) return 'Этот email уже зарегистрирован. Войдите в аккаунт.';
      if (error.status === 422) return 'Проверьте email, пароль и параметры запроса.';
      if (error.status === 403) return 'У вас нет доступа к этому действию.';
      if (error.status === 429)
        return 'Слишком много запросов. Подождите немного и повторите попытку.';
      if (error.status >= 500) return 'Сервис временно недоступен. Попробуйте ещё раз позже.';
      if (error.status === 413) return 'Слишком большой объём данных. Сократите описание.';
      if (error.status === 404) return 'Данные пока не найдены.';
    }
    return 'Не удалось связаться с сервером. Повторите попытку.';
  }

  private persist(): void {
    try {
      sessionStorage.setItem(
        SESSION_KEY,
        JSON.stringify({
          token: this.token,
          email: this.email,
          name: this.name,
          session_id: this.sessionScope(),
        }),
      );
    } catch {
      this.notice.set('Сессия доступна до обновления страницы: хранилище браузера недоступно.');
    }
  }

  private clear(): void {
    this.epoch++;
    this.token = '';
    this.user.set(null);
    this.clearReviewAccess();
    this.sessionScope.set('');
    this.wallet.set(null);
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* In-memory session is cleared. */
    }
  }
}
