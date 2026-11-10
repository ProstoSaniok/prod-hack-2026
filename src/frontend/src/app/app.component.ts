import { DOCUMENT } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import {
  NavigationCancel,
  NavigationEnd,
  NavigationError,
  NavigationStart,
  Router,
  RouterOutlet,
} from '@angular/router';
import { TuiButton, TuiRoot } from '@taiga-ui/core';
import { filter, map, tap } from 'rxjs';
import { LeavePageService } from './shared/leave-page.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, TuiButton, TuiRoot],
  template: `<tui-root>
    @if (navigating()) {
      <progress class="route-progress" aria-label="Открываем страницу…"></progress>
    }
    @if (failedUrl()) {
      <section class="route-error" role="alert" aria-labelledby="route-error-title">
        <div>
          <strong id="route-error-title">Не удалось открыть страницу</strong>
          <p>Возможно, сайт обновился. Перезагрузите его, чтобы открыть выбранную страницу.</p>
          @if (reloadError()) {
            <p>{{ reloadError() }}</p>
          }
        </div>
        <button tuiButton size="m" type="button" [disabled]="reloading()" (click)="reloadPage()">
          {{ reloading() ? 'Проверяем сохранение…' : 'Перезагрузить и открыть' }}
        </button>
      </section>
    }
    <router-outlet />
  </tui-root>`,
  styles: [
    `
      .route-progress {
        position: fixed;
        z-index: 1000;
        inset: 0 0 auto;
        width: 100%;
        height: 4px;
        accent-color: var(--prodguild-accent);
      }

      .route-error {
        position: fixed;
        z-index: 1000;
        inset: 16px 16px auto;
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        align-items: center;
        box-sizing: border-box;
        max-width: 680px;
        margin-inline: auto;
        padding: 20px;
        border: 1px solid var(--prodguild-text);
        border-radius: 20px;
        background: var(--prodguild-surface, #f9f9f9);
        color: var(--prodguild-text);
      }

      .route-error div {
        flex: 1 1 280px;
        min-width: 0;
      }

      .route-error p {
        margin: 8px 0 0;
        overflow-wrap: anywhere;
      }

      .route-error button {
        max-width: 100%;
        white-space: normal;
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  private readonly router = inject(Router);
  private readonly leave = inject(LeavePageService);
  private readonly window = inject(DOCUMENT).defaultView;
  readonly failedUrl = signal<string | null>(null);
  readonly reloading = signal(false);
  readonly reloadError = signal('');
  readonly navigating = toSignal(
    this.router.events.pipe(
      filter(
        event =>
          event instanceof NavigationStart ||
          event instanceof NavigationEnd ||
          event instanceof NavigationCancel ||
          event instanceof NavigationError,
      ),
      tap(event => {
        if (event instanceof NavigationStart || event instanceof NavigationEnd) {
          this.failedUrl.set(null);
          this.reloadError.set('');
        } else if (event instanceof NavigationError && this.isModuleLoadFailure(event.error)) {
          this.failedUrl.set(this.router.serializeUrl(this.router.parseUrl(event.url)));
        }
      }),
      map(event => event instanceof NavigationStart),
    ),
    { initialValue: false },
  );

  async reloadPage(): Promise<void> {
    const url = this.failedUrl();
    const window = this.window;
    if (!url || !window || this.reloading() || this.navigating()) return;
    const target = new URL(url, window.location.origin);
    if (target.origin !== window.location.origin) return;
    this.reloading.set(true);
    this.reloadError.set('');
    try {
      if ((await this.leave.confirmLeave()) && this.failedUrl() === url && !this.navigating())
        window.location.assign(target.href);
    } catch {
      if (this.failedUrl() === url)
        this.reloadError.set('Не удалось проверить сохранение. Проверьте несохранённые изменения.');
    } finally {
      this.reloading.set(false);
    }
  }

  private isModuleLoadFailure(error: unknown): boolean {
    if (!(error instanceof Error)) return false;
    return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|loading chunk .+ failed/i.test(
      error.message,
    );
  }
}
