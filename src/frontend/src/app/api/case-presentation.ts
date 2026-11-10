// Root product.md P06 and backend wallet policy: once per successful participant/case.
// Display only; earning credits is owned by server finalization.
export const SUCCESSFUL_CASE_REWARD = 3;

const skillValueFormatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

export function formatSkillValue(value: number): string {
  return skillValueFormatter.format(value);
}

export const CASE_DIFFICULTIES = [
  { value: 'review_minus', label: 'Лёгкий' },
  { value: 'review_plus', label: 'Средний' },
  { value: 'expert', label: 'Сложный' },
] as const;

export type CaseDifficulty = (typeof CASE_DIFFICULTIES)[number]['value'];

export function isCaseDifficulty(value: string): value is CaseDifficulty {
  return CASE_DIFFICULTIES.some(level => level.value === value);
}

export function caseLevelLabel(difficulty: string): string {
  return CASE_DIFFICULTIES.find(level => level.value === difficulty)?.label ?? 'Уровень не указан';
}

export const CASE_SKILL_LEVELS = [
  { value: 0, label: 'Лёгкий' },
  { value: 25, label: 'Средний' },
  { value: 50, label: 'Сложный' },
  { value: 75, label: 'Экспертный' },
] as const;

export function caseSkillLevelLabel(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0 || value > 100)
    return 'Выберите сложность';
  return [...CASE_SKILL_LEVELS].reverse().find(level => value >= level.value)?.label ?? '';
}

// P02: the maximum is a score of 100 and reviewer expertise of 100.
// Actual XP is calculated by the server after a successful review.
export function maximumSkillXp(weight: number): string {
  return formatSkillValue(100 * weight * 1.2);
}

export function projectStage(status: string): number {
  switch (status) {
    case 'active':
      return 0;
    case 'submitted':
      return 1;
    case 'completed':
      return 2;
    default:
      return -1;
  }
}

export function projectStatusLabel(status: string): string {
  if (status === 'approved') return 'Принято';
  if (status === 'changes_requested') return 'Нужны изменения';
  return ['В работе', 'На проверке', 'Результат'][projectStage(status)] ?? 'Статус недоступен';
}
