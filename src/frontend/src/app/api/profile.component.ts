import { DatePipe } from '@angular/common';
import {
  afterRenderEffect,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { gradeNumber } from '../shared/grade-presentation';
import { autoRefresh } from '../shared/auto-refresh';
import { LoadingStateComponent } from '../shared/loading-state.component';
import { CurrencyAmountComponent } from '../shared/currency-amount.component';
import { ProductIconComponent } from '../shared/product-icon.component';
import { DevelopmentService } from './development.service';
import { ProfileActivityComponent } from './profile-activity.component';
import {
  BackendService,
  DevelopmentPolicy,
  GradeGate,
  SkillGrade,
  ProfileSkill,
  Transaction,
  UserProfile,
} from './backend.service';

@Component({
  standalone: true,
  imports: [
    DatePipe,
    RouterLink,
    TuiButton,
    LoadingStateComponent,
    CurrencyAmountComponent,
    ProductIconComponent,
    ProfileActivityComponent,
  ],
  templateUrl: './profile.component.html',
  styleUrl: './profile.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProfileComponent {
  readonly api = inject(BackendService);
  readonly development = inject(DevelopmentService);
  private readonly router = inject(Router);
  readonly applicationBusy = signal('');
  readonly applicationSkill = signal('');
  readonly applicationError = signal('');
  private readonly destroyRef = inject(DestroyRef);
  private readonly route = inject(ActivatedRoute);
  readonly fragment = toSignal(this.route.fragment, {
    initialValue: this.route.snapshot.fragment,
  });
  private readonly walletSection = viewChild<ElementRef<HTMLElement>>('walletSection');
  private walletNavigationHandled = false;
  private readonly numberFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
  readonly profile = signal<UserProfile | null>(null);
  readonly policy = signal<DevelopmentPolicy | null>(null);
  readonly initializing = signal(true);
  readonly profileLoading = signal(false);
  readonly policyLoading = signal(false);
  readonly historyLoading = signal(false);
  readonly profileError = signal('');
  readonly policyError = signal('');
  readonly historyError = signal('');
  readonly transactions = signal<Transaction[]>([]);
  readonly transactionOffset = signal(0);
  private requestedHistoryOffset = 0;
  readonly gradeNumber = gradeNumber;
  readonly expertDots = Array.from({ length: 32 }, (_, index) => ({
    index,
    left: 3 + ((index * 37) % 94),
    top: 18 + ((index * 29) % 64),
    size: (index % 5 === 0 ? 5 : 2 + (index % 3) * 0.6) * 1.15,
    glint: index % 5 === 0,
    x: ((index * 13) % 27) - 13,
    y: ((index * 7) % 9) - 4,
    spreadX: ((index * 5) % 11) - 5,
    spreadY: ((index * 3) % 7) - 3,
    duration: 5600 + ((index * 619) % 4200),
    delay: -((index * 977) % 9000),
  }));
  readonly labels: Readonly<Record<string, string>> = {
    welcome: 'Приветственный бонус',
    case_reward: 'Награда за кейс',
    successful_case: 'Награда за кейс',
    review_reward: 'Награда за ревью',
    priority_reserve: 'Резерв приоритета',
    priority_release: 'Возврат резерва',
    priority_settle: 'Оплата приоритета',
    priority_refund: 'Возврат оплаты',
  };

  constructor() {
    autoRefresh(
      async () => {
        await Promise.all([this.loadProfile(), this.loadHistory()]);
      },
      {
        blocked: () =>
          this.initializing() ||
          this.profileLoading() ||
          this.historyLoading() ||
          !!this.applicationBusy(),
        shouldPoll: () =>
          this.profile()?.competencies.some(
            skill =>
              !!skill.expert_application?.pending_application_id ||
              (!!skill.probation && !skill.probation.passed),
          ) ?? false,
      },
    );
    afterRenderEffect(() => {
      if (this.fragment() !== 'wallet') {
        this.walletNavigationHandled = false;
        return;
      }
      if (
        this.walletNavigationHandled ||
        this.initializing() ||
        this.profileLoading() ||
        this.policyLoading() ||
        !this.profile()
      )
        return;
      const wallet = this.walletSection()?.nativeElement;
      if (!wallet) return;
      // Correct the initial router anchor after async skills change the layout, once per visit.
      this.walletNavigationHandled = true;
      wallet.scrollIntoView({ block: 'start', behavior: 'instant' });
    });
    void this.initialize();
  }

  formatAmount(value: number): string {
    return this.numberFormat.format(value);
  }

  xpLabel(skill: ProfileSkill): string {
    return `${this.formatAmount(skill.value)} XP`;
  }

  gradeLabel(grade: SkillGrade): string {
    return {
      performer: 'Исполнитель',
      review_minus: 'Ревьюер−',
      review_plus: 'Ревьюер+',
      expert: 'Эксперт',
    }[grade];
  }

  nextGate(skill: ProfileSkill): GradeGate | null {
    const next = {
      performer: 'review_minus',
      review_minus: 'review_plus',
      review_plus: null,
      expert: null,
    }[skill.grade];
    return this.policy()?.grade_gates.find(gate => gate.grade === next) ?? null;
  }

  expertGate(): GradeGate | null {
    return this.policy()?.grade_gates.find(gate => gate.grade === 'expert') ?? null;
  }

  progressMaximum(skill: ProfileSkill): number {
    const next = this.nextGate(skill);
    const current = this.policy()?.grade_gates.find(gate => gate.grade === skill.grade);
    return next && current ? next.minimum_value - current.minimum_value : 1;
  }

  remainingXp(skill: ProfileSkill): number {
    return (
      skill.next_grade?.xp_remaining ??
      Math.max(
        0,
        ((skill.grade === 'review_plus' ? this.expertGate() : this.nextGate(skill))
          ?.minimum_value ?? 0) - skill.contribution_sum,
      )
    );
  }

  progressValue(skill: ProfileSkill, maximum: number): number {
    return skill.grade === 'expert' || skill.grade === 'review_plus'
      ? maximum
      : Math.max(0, Math.min(maximum, maximum - this.remainingXp(skill)));
  }

  remainingLabel(skill: ProfileSkill): string {
    const value = this.remainingXp(skill);
    return value > 0 && value < 0.01 ? 'меньше 0,01' : this.formatAmount(value);
  }

  progressDescription(skill: ProfileSkill): string {
    const next = this.nextGate(skill);
    return next
      ? `До уровня «${this.gradeLabel(next.grade)}»: ${this.remainingLabel(skill)} XP.`
      : skill.grade === 'review_plus'
        ? 'Ревьюер+ — максимальный уровень ревьюера.'
        : 'Эксперт — максимальный уровень.';
  }

  private async initialize(): Promise<void> {
    await this.api.initialized;
    if (this.destroyRef.destroyed) return;
    this.initializing.set(false);
    if (this.api.user())
      await Promise.all([this.loadProfile(), this.loadPolicy(), this.loadHistory()]);
  }

  async loadProfile(): Promise<void> {
    if (this.profileLoading() || !this.api.user()) return;
    const owner = this.api.user()?.id;
    this.profileLoading.set(true);
    this.profileError.set('');
    try {
      const profile = await this.api.profile();
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner) this.profile.set(profile);
      await this.api.refreshReviewAccess();
    } catch (error) {
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner)
        this.profileError.set(this.api.errorMessage(error));
    } finally {
      if (!this.destroyRef.destroyed) this.profileLoading.set(false);
    }
  }

  async loadPolicy(): Promise<void> {
    if (this.policyLoading() || !this.api.user()) return;
    const owner = this.api.user()?.id;
    this.policyLoading.set(true);
    this.policyError.set('');
    try {
      const policy = await this.api.developmentPolicy();
      const gates = [...policy.grade_gates].sort(
        (a, b) => gradeNumber(a.grade) - gradeNumber(b.grade),
      );
      if (
        gates[0]?.minimum_value !== 0 ||
        gates.some(
          (gate, index) => index > 0 && gate.minimum_value <= gates[index - 1].minimum_value,
        )
      )
        throw new Error('Шкала навыков недоступна.');
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner) this.policy.set(policy);
    } catch (error) {
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner)
        this.policyError.set(this.api.errorMessage(error));
    } finally {
      if (!this.destroyRef.destroyed) this.policyLoading.set(false);
    }
  }

  async loadHistory(offset = this.transactionOffset()): Promise<void> {
    if (this.historyLoading() || !this.api.user()) return;
    const owner = this.api.user()?.id;
    this.requestedHistoryOffset = offset;
    this.historyLoading.set(true);
    this.historyError.set('');
    try {
      const items = await this.api.transactions(offset);
      if (this.destroyRef.destroyed || this.api.user()?.id !== owner) return;
      this.transactions.set(items);
      this.transactionOffset.set(offset);
    } catch (error) {
      if (!this.destroyRef.destroyed && this.api.user()?.id === owner)
        this.historyError.set(this.api.errorMessage(error));
    } finally {
      if (!this.destroyRef.destroyed) this.historyLoading.set(false);
    }
  }

  async refreshHistory(): Promise<void> {
    await this.loadHistory(
      this.historyError() ? this.requestedHistoryOffset : this.transactionOffset(),
    );
  }

  async page(direction: number): Promise<void> {
    await this.loadHistory(Math.max(0, this.transactionOffset() + direction * 20));
  }
  hasPendingApplication(skill: ProfileSkill): boolean {
    return this.development.mutations.pending(
      this.development.applicationAction(skill.competency_id),
    );
  }
  async apply(skill: ProfileSkill, retry = false): Promise<void> {
    if (this.applicationBusy()) return;
    this.applicationBusy.set(skill.competency_id);
    this.applicationSkill.set(skill.competency_id);
    this.applicationError.set('');
    try {
      const id = retry
        ? await this.development.retryApplication(skill.competency_id)
        : await this.development.apply(skill);
      if (!this.destroyRef.destroyed) await this.router.navigate(['/reviewer-applications', id]);
    } catch (error) {
      if (!this.destroyRef.destroyed) this.applicationError.set(this.development.message(error));
    } finally {
      if (!this.destroyRef.destroyed) this.applicationBusy.set('');
    }
  }
}
