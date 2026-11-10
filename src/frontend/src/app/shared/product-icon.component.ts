import { ChangeDetectionStrategy, Component, input } from '@angular/core';

const ICON_PATHS = {
  file: 'M6 3h8l4 4v14H6Z M14 3v5h4 M9 12h6 M9 16h6',
  'file-plus': 'M13 21H6V3h8l4 4v5 M14 3v5h4 M18 15v6 M15 18h6',
  folder: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z',
  'folder-open': 'M3 17V7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v2 M3 19l3-8h16l-3 8Z',
  'chevron-right': 'm9 5 7 7-7 7',
  'chevron-down': 'm5 9 7 7 7-7',
  x: 'm6 6 12 12 M18 6 6 18',
  archive: 'M3 4h18v4H3Z M5 8v13h14V8 M10 12h4',
  upload: 'M12 16V3 m-5 5 5-5 5 5 M4 16v5h16v-5',
  'shield-check': 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6Z m-4 9 3 3 5-6',
  comment: 'M4 4h16v12H9l-5 4Z M8 8h8 M8 12h5',
  check: 'm5 12 4 4L19 6',
  'arrow-left': 'M20 12H4 m6-6-6 6 6 6',
  'arrow-right': 'M4 12h16 m-6-6 6 6-6 6',
  'arrow-up': 'M12 20V4 m-6 6 6-6 6 6',
  'arrow-down': 'M12 4v16 m-6-6 6 6 6-6',
  arrows: 'M4 8h16 m-4-4 4 4-4 4 M20 16H4 m4-4-4 4 4 4',
  plus: 'M12 4v16 M4 12h16',
  refresh: 'M20 8a8 8 0 1 0 0 8 M20 3v5h-5',
  download: 'M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5',
  save: 'M4 3h13l3 3v15H4Z M8 3v6h8V3 M8 21v-8h8v8',
  trash: 'M3 6h18 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7',
  expand: 'M9 3H3v6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6',
  collapse: 'M3 9h6V3 M21 9h-6V3 M15 21v-6h6 M9 21v-6H3',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l4 2',
  wallet: 'M4 6V4h14v3 M4 7h16v14H4Z M20 11h-6v6h6 M17 14h.01',
  award: 'M12 3a6 6 0 1 0 0 12 6 6 0 0 0 0-12 M8 14l-2 7 6-3 6 3-2-7',
} as const;

export type ProductIconName = keyof typeof ICON_PATHS;

/** Decorative contours; the containing action or heading supplies its accessible name. */
@Component({
  selector: 'app-product-icon',
  standalone: true,
  template: '<svg viewBox="0 0 24 24" aria-hidden="true"><path [attr.d]="paths[name()]" /></svg>',
  styles: `
    :host {
      display: inline-flex;
      flex: 0 0 auto;
      width: 1.15em;
      height: 1.15em;
      vertical-align: -0.15em;
      pointer-events: none;
    }
    svg {
      width: 100%;
      height: 100%;
      fill: none;
      stroke: currentcolor;
      stroke-width: 1.7;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
  `,
  host: { 'aria-hidden': 'true' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProductIconComponent {
  readonly name = input.required<ProductIconName>();
  protected readonly paths = ICON_PATHS;
}
