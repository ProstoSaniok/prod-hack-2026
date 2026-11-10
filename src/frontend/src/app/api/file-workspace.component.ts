import { ChangeDetectionStrategy, Component, input, output, viewChild } from '@angular/core';
import { RepositoryWriteContext } from './project-autosave.service';
import { RemoteFilesComponent } from './remote-files.component';
import { WorkspaceMaximizeDirective } from '../shared/workspace-maximize/workspace-maximize.directive';
import { ProductIconComponent } from '../shared/product-icon.component';
import { TuiButton } from '@taiga-ui/core';

/** The same file workspace serves participant solutions and organizer repositories. */
@Component({
  selector: 'app-file-workspace',
  standalone: true,
  imports: [RemoteFilesComponent, WorkspaceMaximizeDirective, ProductIconComponent, TuiButton],
  template: `
    <section
      class="workspace"
      [attr.aria-label]="title()"
      appWorkspaceMaximize
      #workspace="workspaceMaximize"
    >
      <header class="maximize-toolbar">
        <div>
          <h2>{{ title() }}</h2>
          @if (description()) {
            <p>{{ description() }}</p>
          }
        </div>
        <div class="actions"><ng-content /></div>
        @if (attachmentsEnabled()) {
          <button
            #expand
            tuiButton
            appearance="flat"
            size="s"
            type="button"
            [attr.aria-expanded]="workspace.expanded()"
            [attr.aria-controls]="workspace.id"
            (click)="workspace.toggle(expand)"
          >
            <app-product-icon [name]="workspace.expanded() ? 'collapse' : 'expand'" />
            {{ workspace.expanded() ? 'Свернуть код' : 'Развернуть код' }}
          </button>
        }
      </header>
      <app-remote-files
        [prefix]="prefix()"
        [versions]="versions()"
        [readOnly]="readOnly()"
        [allowEmpty]="allowEmpty()"
        [autosave]="autosave()"
        [writeContext]="writeContext()"
        [allowMaximize]="!attachmentsEnabled()"
        (changed)="changed.emit()"
        (refreshRequested)="changed.emit()"
        (unsettled)="unsettled.emit($event)"
        (dirty)="dirty.emit($event)"
        (working)="working.emit($event)"
      />
    </section>
  `,
  styles: `
    :host {
      display: block;
      min-width: 0;
    }
    .workspace {
      padding: clamp(16px, 2.5vw, 28px);
      border-radius: var(--prodguild-radius-lg);
      background: var(--prodguild-surface);
      min-width: 0;
    }
    header {
      display: flex;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 16px;
    }
    header > div:first-child {
      flex: 1;
      min-width: min(100%, 240px);
    }
    h2 {
      margin: 0;
      font-size: var(--prodguild-font-section);
      font-weight: 500;
    }
    p {
      margin: 8px 0 0;
      line-height: 1.5;
      color: var(--prodguild-text-secondary);
      max-width: 72ch;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .workspace[data-code-maximize-target] > app-remote-files {
      flex: 1;
      min-height: 0;
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FileWorkspaceComponent {
  // Legacy binding now only preserves the consumer's maximize target, not attachment support.
  readonly attachmentsEnabled = input(false);
  readonly title = input('Файлы решения');
  readonly description = input('');
  readonly prefix = input.required<string>();
  readonly versions = input('');
  readonly readOnly = input(false);
  readonly allowEmpty = input(false);
  readonly autosave = input(false);
  readonly writeContext = input<RepositoryWriteContext>();
  private readonly files = viewChild(RemoteFilesComponent);
  async flush(): Promise<boolean> {
    return !!(await this.files()?.flush());
  }
  hasPendingChanges(): boolean {
    return this.files()?.hasPendingChanges() ?? false;
  }
  readonly changed = output<void>();
  readonly unsettled = output<boolean>();
  readonly dirty = output<boolean>();
  readonly working = output<boolean>();
}
