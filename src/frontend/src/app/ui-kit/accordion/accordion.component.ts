import { ChangeDetectionStrategy, Component, input, model } from '@angular/core';
import { TuiButton, TuiExpand } from '@taiga-ui/core';

@Component({
  selector: 'app-accordion',
  standalone: true,
  imports: [TuiButton, TuiExpand],
  templateUrl: './accordion.component.html',
  styleUrl: './accordion.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccordionComponent {
  readonly panelId = input.required<string>();
  readonly heading = input.required<string>();
  readonly expanded = model(false);

  toggle(): void {
    this.expanded.update(value => !value);
  }

  close(): void {
    this.expanded.set(false);
  }
}
