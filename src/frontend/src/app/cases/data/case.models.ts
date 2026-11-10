export type CaseCategory = 'Аналитика' | 'Дизайн' | 'Разработка';
export type CaseLevel = 'Начальный' | 'Средний' | 'Продвинутый';

export interface CaseStudy {
  readonly id: string;
  readonly title: string;
  readonly category: CaseCategory;
  readonly level: CaseLevel;
  readonly duration: string;
  readonly description: string;
  readonly skills: readonly string[];
  readonly context: string;
  readonly tasks: readonly string[];
  readonly criteria: readonly string[];
  readonly material: string;
}

export interface CaseWork {
  readonly caseId: string;
  readonly status: 'started' | 'submitted' | 'completed';
  readonly draft: string;
  readonly steps: readonly number[];
  readonly updatedAt: string;
}

export interface MockAccount {
  readonly login: string;
  readonly name: string;
  readonly salt: string;
  readonly passwordHash: string;
}

export type User = Pick<MockAccount, 'login' | 'name'>;
