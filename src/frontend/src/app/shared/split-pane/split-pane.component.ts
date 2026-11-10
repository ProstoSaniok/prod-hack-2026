import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  inject,
  input,
  NgZone,
  output,
  viewChild,
} from '@angular/core';

/** A persistent two-pane layout: resizing never recreates projected content. */
@Component({
  selector: 'app-split-pane',
  standalone: true,
  templateUrl: './split-pane.component.html',
  styleUrl: './split-pane.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SplitPaneComponent {
  readonly primaryId = input.required<string>();
  readonly secondaryId = input.required<string>();
  readonly compactChange = output<boolean>();
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly zone = inject(NgZone);
  private readonly handle = viewChild.required<ElementRef<HTMLElement>>('handle');
  private readonly minimum = 280;
  private readonly secondaryMinimum = 420;
  private readonly handleWidth = 16;
  private ratio = 0.3;
  private width = 0;
  private primaryWidth = 280;
  private compact: boolean | undefined;
  private pointer: number | undefined;
  private grabOffset = 0;
  private observer?: ResizeObserver;
  private readonly listeners = new AbortController();

  constructor() {
    afterNextRender(() => {
      this.zone.runOutsideAngular(() => {
        const handle = this.handle().nativeElement;
        const options = { signal: this.listeners.signal };
        handle.addEventListener('pointerdown', event => this.start(event), options);
        handle.addEventListener('pointermove', event => this.move(event), options);
        handle.addEventListener('pointerup', () => this.stop(), options);
        handle.addEventListener('pointercancel', () => this.stop(), options);
        handle.addEventListener('lostpointercapture', () => this.stop(), options);
        handle.addEventListener('keydown', event => this.keydown(event), options);
        handle.addEventListener('dblclick', () => this.reset(), options);
        this.observer = new ResizeObserver(entries => {
          this.width = entries[0].contentRect.width;
          this.layout();
        });
        this.observer.observe(this.host);
      });
    });
    inject(DestroyRef).onDestroy(() => {
      this.stop();
      this.observer?.disconnect();
      this.listeners.abort();
    });
  }

  private get available(): number {
    return Math.max(0, this.width - this.handleWidth);
  }

  private layout(): void {
    const compact = this.available < this.minimum + this.secondaryMinimum;
    if (compact !== this.compact) {
      this.compact = compact;
      this.stop();
      const handle = this.handle().nativeElement;
      if (compact && document.activeElement === handle) {
        document.getElementById(this.primaryId())?.querySelector<HTMLElement>('button')?.focus();
      }
      handle.hidden = compact;
      this.host.classList.toggle('compact', compact);
      this.zone.run(() => this.compactChange.emit(compact));
    }
    if (compact) return;
    this.primaryWidth = this.clamp(this.available * this.ratio);
    this.host.style.setProperty('--split-primary-width', `${this.primaryWidth}px`);
    const handle = this.handle().nativeElement;
    handle.setAttribute('aria-valuemin', String(this.minimum));
    handle.setAttribute(
      'aria-valuemax',
      String(Math.round(this.available - this.secondaryMinimum)),
    );
    handle.setAttribute('aria-valuenow', String(Math.round(this.primaryWidth)));
    handle.setAttribute(
      'aria-valuetext',
      `Панель информации: ${Math.round(this.primaryWidth)} пикселей, ${Math.round((this.primaryWidth / this.available) * 100)}%`,
    );
  }

  private clamp(width: number): number {
    return Math.max(this.minimum, Math.min(this.available - this.secondaryMinimum, width));
  }

  private resize(width: number): void {
    if (this.compact || !this.available) return;
    this.ratio = this.clamp(width) / this.available;
    this.layout();
  }

  private start(event: PointerEvent): void {
    if (this.compact || !event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    this.pointer = event.pointerId;
    this.grabOffset = event.clientX - this.host.getBoundingClientRect().left - this.primaryWidth;
    const handle = this.handle().nativeElement;
    handle.focus({ preventScroll: true });
    handle.setPointerCapture(event.pointerId);
    this.host.classList.add('dragging');
  }

  private move(event: PointerEvent): void {
    if (event.pointerId !== this.pointer) return;
    this.resize(event.clientX - this.host.getBoundingClientRect().left - this.grabOffset);
  }

  private stop(): void {
    const pointer = this.pointer;
    this.pointer = undefined;
    this.host.classList.remove('dragging');
    if (pointer === undefined) return;
    const handle = this.handle().nativeElement;
    if (handle.hasPointerCapture(pointer)) handle.releasePointerCapture(pointer);
  }

  private reset(): void {
    this.ratio = 0.3;
    this.layout();
  }

  private keydown(event: KeyboardEvent): void {
    const step = event.shiftKey ? 48 : 16;
    switch (event.key) {
      case 'ArrowLeft':
        this.resize(this.primaryWidth - step);
        break;
      case 'ArrowRight':
        this.resize(this.primaryWidth + step);
        break;
      case 'Home':
        this.resize(this.minimum);
        break;
      case 'End':
        this.resize(this.available - this.secondaryMinimum);
        break;
      case 'Enter':
        this.reset();
        break;
      default:
        return;
    }
    event.preventDefault();
  }
}
