import {
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { TuiButton, TuiDropdown } from '@taiga-ui/core';

export interface CloneRepository {
  readonly httpsUrl: string;
  readonly sshUrl: string;
  readonly mock?: boolean;
}

@Component({
  selector: 'app-repository-clone',
  standalone: true,
  imports: [TuiButton, TuiDropdown],
  templateUrl: './repository-clone.component.html',
  styleUrl: './repository-clone.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class RepositoryCloneComponent {
  private readonly trigger = viewChild.required<ElementRef<HTMLButtonElement>>('cloneTrigger');
  readonly repository = input.required<CloneRepository>();
  readonly open = signal(false);
  readonly protocol = signal<'https' | 'ssh'>('https');
  readonly status = signal('');
  readonly url = computed(() =>
    this.protocol() === 'https' ? this.repository().httpsUrl : this.repository().sshUrl,
  );
  select(protocol: 'https' | 'ssh'): void {
    this.protocol.set(protocol);
    this.status.set('');
  }
  close(): void {
    this.open.set(false);
    this.trigger().nativeElement.focus();
  }
  async copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.url());
      this.status.set('Ссылка скопирована');
    } catch {
      this.status.set('Выделите и скопируйте ссылку вручную.');
    }
  }
}
