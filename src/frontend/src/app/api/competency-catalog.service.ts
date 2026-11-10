import { computed, effect, inject, Injectable, signal, untracked } from '@angular/core';
import { ApiUser, BackendService, Competency } from './backend.service';

interface CatalogueScope {
  key: string;
  user: ApiUser | null;
}

/** A session-scoped catalogue: each explicit request reads at most one page. */
@Injectable({ providedIn: 'root' })
export class CompetencyCatalogService {
  private readonly api = inject(BackendService);
  readonly items = signal<readonly Competency[]>([]);
  readonly names = computed(() => new Map(this.items().map(item => [item.id, item.name])));
  readonly loading = signal(false);
  readonly error = signal('');
  readonly hasMore = signal(false);
  readonly loaded = signal(false);
  private readonly pageSize = 20;
  private scope: CatalogueScope | null = null;
  private generation = 0;
  private offset = 0;
  private running: Promise<void> | null = null;

  constructor() {
    effect(() => {
      const scope = this.currentScope();
      untracked(() => this.synchronizeScope(scope));
    });
  }

  async ensureLoaded(): Promise<void> {
    await this.api.initialized;
    this.synchronizeScope(this.currentScope());
    // Route/focus refreshes must not retry a failed catalogue request.
    if (this.loaded() || this.error()) return;
    return this.loadNextPage();
  }

  async loadMore(): Promise<void> {
    await this.api.initialized;
    this.synchronizeScope(this.currentScope());
    return this.loadNextPage();
  }

  private currentScope(): CatalogueScope {
    const adminId = this.api.adminUserId();
    if (adminId) return { key: `admin:${adminId}`, user: null };
    const user = this.api.user();
    const session = this.api.sessionScope();
    return {
      key: user ? `participant:${user.id}:${session}` : '',
      // currentUser replaces the object on navigation; the session ID stays stable.
      user: session ? null : user,
    };
  }

  private synchronizeScope(scope: CatalogueScope): void {
    if (this.scope?.key === scope.key && this.scope.user === scope.user) return;
    this.scope = scope;
    this.generation++;
    this.offset = 0;
    this.running = null;
    this.items.set([]);
    this.loading.set(false);
    this.error.set('');
    this.hasMore.set(false);
    this.loaded.set(false);
  }

  private isCurrent(generation: number): boolean {
    this.synchronizeScope(this.currentScope());
    return generation === this.generation;
  }

  private loadNextPage(): Promise<void> {
    if (this.running) return this.running;
    if (this.loaded() && !this.hasMore() && !this.error()) return Promise.resolve();
    if (!this.scope?.key) {
      this.error.set('Войдите, чтобы загрузить навыки.');
      return Promise.resolve();
    }
    const generation = this.generation;
    const offset = this.offset;
    this.loading.set(true);
    this.error.set('');
    const request = (async () => {
      try {
        const page = await this.api.competencies(offset);
        if (!this.isCurrent(generation)) return;
        if (page.length > this.pageSize) throw new Error('Сервер вернул слишком много навыков.');
        const merged = new Map(this.items().map(item => [item.id, item]));
        const previousSize = merged.size;
        for (const item of page) merged.set(item.id, item);
        if (page.length === this.pageSize && merged.size === previousSize) {
          this.hasMore.set(false);
          this.error.set('Сервер повторил страницу навыков. Дальнейшая загрузка остановлена.');
          return;
        }
        this.items.set([...merged.values()]);
        this.offset = offset + this.pageSize;
        this.hasMore.set(page.length === this.pageSize);
        this.loaded.set(true);
      } catch (error) {
        if (this.isCurrent(generation)) this.error.set(this.api.errorMessage(error));
      } finally {
        if (this.isCurrent(generation)) {
          this.loading.set(false);
          this.running = null;
        }
      }
    })();
    this.running = request;
    return request;
  }
}
