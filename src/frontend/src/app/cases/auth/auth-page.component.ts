import { LoadingStateComponent } from '../../shared/loading-state.component';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { TuiButton, TuiTextfield } from '@taiga-ui/core';

import { BackendService } from '../../api/backend.service';
import { safeReturnUrl } from './auth.guard';

@Component({
  selector: 'app-auth-page',
  standalone: true,
  imports: [LoadingStateComponent, ReactiveFormsModule, RouterLink, TuiButton, TuiTextfield],
  templateUrl: './auth-page.component.html',
  styleUrl: './auth-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AuthPageComponent {
  readonly api = inject(BackendService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly hasAdminEntry = this.router.config.some(route => route.path === 'admin');
  readonly isRegistration = signal(false);
  readonly loading = signal(false);
  readonly showPassword = signal(false);
  readonly formError = signal('');
  readonly form = new FormGroup({
    name: new FormControl('', { nonNullable: true }),
    login: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email, Validators.maxLength(320)],
    }),
    password: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.minLength(8), Validators.maxLength(128)],
    }),
    repeatPassword: new FormControl('', { nonNullable: true }),
  });

  constructor() {
    this.route.data.pipe(takeUntilDestroyed()).subscribe(data => {
      this.isRegistration.set(data['mode'] === 'register');
      this.form.controls.name.setValidators(
        this.isRegistration()
          ? [Validators.required, Validators.maxLength(60), Validators.pattern(/\S/)]
          : [],
      );
      this.form.controls.repeatPassword.setValidators(
        this.isRegistration() ? [Validators.required] : [],
      );
      this.form.reset();
      this.form.controls.name.updateValueAndValidity();
      this.form.controls.repeatPassword.updateValueAndValidity();
      this.formError.set('');
      this.showPassword.set(false);
    });
    this.form.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => this.formError.set(''));
  }

  get returnUrl(): string {
    return safeReturnUrl(this.route.snapshot.queryParamMap.get('returnUrl'));
  }

  error(field: keyof typeof this.form.controls): string {
    const control = this.form.controls[field];
    if (!control.touched) return '';
    if (control.hasError('required')) return 'Заполните это поле.';
    if (field === 'name' && control.invalid) return 'Укажите имя от 1 до 60 символов.';
    if (field === 'login' && control.invalid) {
      return 'Введите корректный email.';
    }
    if (field === 'password' && control.invalid)
      return 'Пароль должен содержать от 8 до 128 символов.';
    if (field === 'repeatPassword' && control.value !== this.form.controls.password.value) {
      return 'Пароли не совпадают.';
    }
    return '';
  }

  async submit(): Promise<void> {
    if (this.loading()) return;
    this.form.markAllAsTouched();
    if (this.form.invalid || (this.isRegistration() && this.error('repeatPassword'))) return;
    this.loading.set(true);
    this.formError.set('');
    const { name, login, password } = this.form.getRawValue();
    const requestedUrl = this.router.url;
    const returnUrl = this.returnUrl;
    try {
      await this.api.authenticate(login, password, this.isRegistration(), name);
      if (this.destroyRef.destroyed || this.router.url !== requestedUrl) return;
      await this.router.navigateByUrl(this.api.isOrganizer() ? '/admin' : returnUrl);
    } catch (error) {
      if (!this.destroyRef.destroyed && this.router.url === requestedUrl) {
        this.formError.set(this.api.errorMessage(error));
      }
    } finally {
      this.loading.set(false);
    }
  }

  async openAdmin(event: MouseEvent): Promise<void> {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0)
      return;
    event.preventDefault();
    if (this.loading()) return;
    this.loading.set(true);
    this.formError.set('');
    try {
      await this.api.ensureAdminAccess();
      if (!this.destroyRef.destroyed) await this.router.navigateByUrl('/admin');
    } catch (error) {
      if (!this.destroyRef.destroyed) this.formError.set(this.api.errorMessage(error));
    } finally {
      this.loading.set(false);
    }
  }

  readonly localRoles = [
    { key: 'performer', label: 'Исполнитель' },
    { key: 'review-minus', label: 'Ревьюер−' },
    { key: 'review-plus', label: 'Ревьюер+' },
    { key: 'mentor', label: 'Эксперт' },
  ];

  async openRole(key: string, label: string): Promise<void> {
    if (this.loading()) return;
    this.loading.set(true);
    try {
      this.formError.set('');
      await this.api.authenticate(`${key}.local@example.com`, 'Local-demo-2026!', false, label);
      if (this.destroyRef.destroyed) return;
      await this.router.navigateByUrl(
        this.api.isOrganizer()
          ? '/admin'
          : key === 'performer'
            ? '/catalog'
            : key === 'mentor'
              ? '/expert'
              : '/reviews',
      );
    } catch (error) {
      this.formError.set(this.api.errorMessage(error));
    } finally {
      this.loading.set(false);
    }
  }
}
