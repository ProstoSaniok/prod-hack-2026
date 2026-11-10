export interface ReviewFile {
  readonly path: string;
  readonly content: string;
}

export interface ReviewMessage {
  readonly id: string;
  readonly author: string;
  readonly text: string;
}

export interface ReviewThread extends ReviewMessage {
  readonly path: string;
  /** Line in the immutable example submission, independent of reviewer edits. */
  readonly line: number;
  readonly resolved: boolean;
  readonly replies: readonly ReviewMessage[];
}

export interface ReviewExample {
  readonly id: string;
  readonly title: string;
  readonly author: string;
  readonly initials: string;
  readonly category: string;
  readonly language: string;
  readonly summary: string;
  readonly task: string;
  readonly approach: string;
  readonly files: readonly ReviewFile[];
  readonly threads: readonly ReviewThread[];
}

export interface ReviewDraft {
  readonly files: Readonly<Record<string, string>>;
  readonly threads: readonly ReviewThread[];
}

export class ReviewInputError extends Error {
  constructor(
    readonly field: 'line' | 'text',
    message: string,
  ) {
    super(message);
  }
}
