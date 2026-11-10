import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  input,
  output,
  untracked,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormArray, FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { TuiAppearance, TuiTextfield } from '@taiga-ui/core';
import { Competency } from '../../api/backend.service';
import { SkillSelectComponent } from '../../shared/skill-select/skill-select.component';
import { CompetencyNamesNoticeComponent } from '../../shared/competency-names-notice.component';
import { CaseFormDraft, CaseFormSkill } from '../cases/case.model';
import { maximumSkillXp } from '../../api/case-presentation';

function skillForm(skill: CaseFormSkill) {
  return new FormGroup({
    competency_id: new FormControl(skill.competency_id, { nonNullable: true }),
    required_value: new FormControl<number | null>(skill.required_value, {
      validators: [Validators.required, Validators.min(0), Validators.max(100)],
    }),
    weight: new FormControl<number | null>(skill.weight, {
      validators: [Validators.required, Validators.min(Number.MIN_VALUE), Validators.max(0.25)],
    }),
  });
}
@Component({
  selector: 'app-case-form',
  standalone: true,
  imports: [
    ReactiveFormsModule,
    TuiAppearance,
    TuiTextfield,
    SkillSelectComponent,
    CompetencyNamesNoticeComponent,
  ],
  templateUrl: './case-form.component.html',
  styleUrl: './case-form.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CaseFormComponent {
  readonly maximumXp = maximumSkillXp;
  private readonly levelInputs = new WeakMap<FormControl<number | null>, string>();
  private readonly weightInputs = new WeakMap<FormControl<number | null>, string>();
  readonly initial = input<CaseFormDraft | null>(null);
  readonly competencies = input<readonly Competency[]>([]);
  readonly loading = input(false);
  readonly loadError = input('');
  readonly hasMore = input(false);
  readonly opened = output<void>();
  readonly loadMoreRequested = output<void>();
  readonly busy = input(false);
  readonly conflict = input(false);
  readonly draftChange = output<CaseFormDraft>();
  private readonly nameInput = viewChild.required<ElementRef<HTMLInputElement>>('nameInput');
  readonly form = new FormGroup({
    name: new FormControl('', { nonNullable: true, validators: [Validators.maxLength(500)] }),
    description: new FormControl('', {
      nonNullable: true,
      validators: [Validators.maxLength(3000)],
    }),
    expectedResult: new FormControl('', {
      nonNullable: true,
      validators: [Validators.maxLength(3000)],
    }),
    competencies: new FormArray<ReturnType<typeof skillForm>>([]),
  });
  readonly name = this.form.controls.name;
  readonly description = this.form.controls.description;
  constructor() {
    this.form.valueChanges.pipe(takeUntilDestroyed(inject(DestroyRef))).subscribe(() => {
      // Taiga clears a select to an empty string with Backspace; the persisted
      // draft contract represents an unfinished numeric choice with null.
      for (const skill of this.form.controls.competencies.controls) {
        const required = skill.controls.required_value;
        if (required.value !== null && typeof required.value !== 'number')
          required.setValue(null, { emitEvent: false });
      }
      this.draftChange.emit(this.form.getRawValue());
    });
    afterNextRender(() => this.nameInput().nativeElement.focus());
    effect(() => {
      const value = this.initial();
      untracked(() => {
        this.form.reset(undefined, { emitEvent: false });
        this.form.controls.competencies.clear({ emitEvent: false });
        if (value) {
          this.form.patchValue(
            {
              name: value.name,
              description: value.description,
              expectedResult: value.expectedResult,
            },
            { emitEvent: false },
          );
          value.competencies.forEach(skill =>
            this.form.controls.competencies.push(skillForm(skill), { emitEvent: false }),
          );
        }
        this.form.markAsPristine();
      });
    });
  }
  get selectedIds(): readonly string[] {
    return this.form.controls.competencies.getRawValue().map(skill => skill.competency_id);
  }
  selectSkills(ids: readonly string[]): void {
    const previous = [...this.form.controls.competencies.controls];
    this.form.controls.competencies.clear({ emitEvent: false });
    for (const id of ids) {
      this.form.controls.competencies.push(
        previous.find(skill => skill.controls.competency_id.value === id) ??
          skillForm({
            competency_id: id,
            required_value: 50,
            weight: 0.1,
          }),
        { emitEvent: false },
      );
    }
    this.form.markAsDirty();
    this.draftChange.emit(this.form.getRawValue());
  }
  skillName(id: string): string {
    return (
      this.competencies().find(skill => skill.id === id)?.name ??
      `Навык ${this.selectedIds.indexOf(id) + 1}`
    );
  }
  levelInput(control: FormControl<number | null>): string {
    return (
      this.levelInputs.get(control) ??
      (control.value === null ? '' : String(control.value).replace('.', ','))
    );
  }
  updateLevel(control: FormControl<number | null>, event: Event): void {
    if (!(event.target instanceof HTMLInputElement)) return;
    const raw = event.target.value;
    this.levelInputs.set(control, raw);
    const normalized = raw.trim().replace(',', '.');
    const value = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(normalized)
      ? Number(normalized)
      : NaN;
    const underflow = value === 0 && /[1-9]/.test(normalized.split(/e/i)[0] ?? '');
    control.markAsDirty();
    control.setValue(Number.isFinite(value) && !underflow ? value : null);
  }
  weightPercent(control: FormControl<number | null>): string {
    return (
      this.weightInputs.get(control) ??
      (control.value === null
        ? ''
        : Number((control.value * 100).toPrecision(15))
            .toString()
            .replace('.', ','))
    );
  }
  updateWeight(control: FormControl<number | null>, event: Event): void {
    if (!(event.target instanceof HTMLInputElement)) return;
    const raw = event.target.value;
    this.weightInputs.set(control, raw);
    const normalized = raw.trim().replace(',', '.');
    const percent = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized) ? Number(normalized) : NaN;
    control.markAsDirty();
    control.setValue(Number.isFinite(percent) ? percent / 100 : null);
  }
}
