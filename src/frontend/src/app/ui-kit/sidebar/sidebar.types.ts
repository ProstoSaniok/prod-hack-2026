/** A destination in the consuming application's navigation. */
export interface SidebarItem {
  readonly id: string;
  readonly label: string;
  /** An application route or a fragment such as #projects. */
  readonly href: string;
  /** SVG path data, rendered within a 24 × 24 view box. */
  readonly icon: string;
}
