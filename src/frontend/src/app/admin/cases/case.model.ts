export interface CaseDraft {
  readonly name: string;
  readonly description: string;
  readonly expectedResult: string;
  readonly competencies: readonly CaseSkill[];
}

export type CaseMetadataPatch = Pick<CaseDraft, 'name' | 'description' | 'expectedResult'> & {
  readonly competencies?: readonly CaseSkill[];
};

export function validRequiredValue(value: number | null): value is number {
  return value !== null && Number.isFinite(value) && value >= 0 && value <= 100;
}

export interface CaseSkill {
  readonly competency_id: string;
  readonly required_value: number;
  readonly weight: number;
}

export interface CaseRecord extends CaseDraft {
  readonly id: string;
  readonly createdAt: string;
  readonly version: number;
  readonly status: 'draft' | 'published' | 'archived';
  readonly difficulty: 'performer' | 'review_minus' | 'review_plus' | 'expert';
  readonly frozen: boolean;
}

export interface CaseFormSkill {
  readonly competency_id: string;
  readonly required_value: number | null;
  readonly weight: number | null;
}

export interface CaseFormDraft {
  readonly name: string;
  readonly description: string;
  readonly expectedResult: string;
  readonly competencies: readonly CaseFormSkill[];
}
