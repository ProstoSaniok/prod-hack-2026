import type { DevelopmentPolicy, SkillGrade } from '../api/backend.service';

const grades: readonly SkillGrade[] = ['performer', 'review_minus', 'review_plus', 'expert'];

/** Presentation only: the stored grade remains authoritative at every XP threshold. */
export function gradeNumber(grade: SkillGrade): number {
  return grades.indexOf(grade) + 1;
}

export function expertGradeMaximum(policy: DevelopmentPolicy): number | null {
  const maximum = policy.grade_gates.find(gate => gate.grade === 'expert')?.minimum_value;
  return maximum !== undefined && Number.isFinite(maximum) && maximum > 0 ? maximum : null;
}
