import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatProditsAmount, proditsUnit } from './prodits-presentation';

@Component({
  selector: 'app-currency-amount',
  standalone: true,
  template: `
    <span class="number-clip"
      ><span class="currency-number">{{ formatted() }}</span></span
    >
    <img src="/imgs/logos/logo-currency.svg" width="100" height="60" alt="" aria-hidden="true" />
    <span class="currency-unit"> {{ unit() }}</span>
  `,
  styles: `
    :host {
      display: inline-flex;
      align-items: baseline;
      flex-wrap: nowrap;
      gap: 0.25em;
      max-width: 100%;
      min-width: 0;
      vertical-align: baseline;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
      transition: gap var(--currency-motion-duration, 0ms) var(--currency-motion-easing, ease);
    }

    .number-clip {
      display: grid;
      grid-template-columns: 1fr;
      min-width: 0;
      transition: grid-template-columns var(--currency-motion-duration, 0ms)
        var(--currency-motion-easing, ease);
    }

    .currency-number {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    img {
      display: block;
      flex: 0 0 auto;
      width: auto;
      height: 0.67em;
      aspect-ratio: 100 / 60;
      object-fit: contain;
    }

    .currency-unit {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
    }

    :host(.compact) {
      gap: 0;
    }

    :host(.compact) .number-clip {
      grid-template-columns: 0fr;
    }
  `,
  host: {
    '[class.compact]': 'compact()',
    '[attr.title]': "formatted() + ' ' + unit()",
  },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CurrencyAmountComponent {
  readonly value = input.required<number | null>();
  readonly showPlus = input(false);
  readonly compact = input(false);
  readonly formatted = computed(() => formatProditsAmount(this.value(), this.showPlus()));
  readonly unit = computed(() => proditsUnit(this.value()));
}
