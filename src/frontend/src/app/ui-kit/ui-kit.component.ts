import { ChangeDetectionStrategy, Component } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TuiAppearance, TuiButton, TuiTextfield } from '@taiga-ui/core';

import { SiteHeaderComponent } from '../shared/site-header/site-header.component';

import { AccordionComponent } from './accordion/accordion.component';

@Component({
  selector: 'app-ui-kit',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    TuiAppearance,
    TuiButton,
    TuiTextfield,
    AccordionComponent,
    SiteHeaderComponent,
  ],
  templateUrl: './ui-kit.component.html',
  styleUrl: './ui-kit.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class UiKitComponent {
  readonly email = new FormControl('example', {
    nonNullable: true,
    validators: [Validators.required, Validators.email],
  });
}
