export type ProjectStatus = 'in_progress' | 'submitted' | 'completed';

export interface BinaryAttachment {
  readonly base64: string;
  readonly mimeType: string;
  readonly size: number;
}

export interface UploadedFile {
  readonly path: string;
  readonly content: string;
  readonly binary?: BinaryAttachment;
}

export interface SolutionFile {
  readonly path: string;
  /** Local draft, independent of the last saved mock commit. */
  readonly content: string;
  readonly savedContent: string;
  readonly sha: string;
  readonly binary?: BinaryAttachment;
}

export interface SubmissionSnapshot {
  readonly id: string;
  readonly commitSha: string;
  readonly submittedAt: string;
  readonly files: readonly UploadedFile[];
}

export interface PublishedFeedback {
  readonly example: true;
  readonly score: number;
  readonly verdict: 'approved' | 'changes_requested';
  readonly strengths: string;
  readonly problems: string;
  readonly nextSteps: string;
}

export interface SolutionProject {
  readonly id: string;
  readonly ownerId: string;
  readonly caseId: string;
  readonly status: ProjectStatus;
  readonly commitSha: string;
  readonly updatedAt: string;
  readonly files: readonly SolutionFile[];
  readonly snapshot: SubmissionSnapshot | null;
  readonly feedback: PublishedFeedback | null;
}

export interface SolutionRepository {
  openProject(ownerId: string, caseId: string): Promise<SolutionProject>;
  listFiles(ownerId: string, projectId: string): Promise<readonly SolutionFile[]>;
  readFile(ownerId: string, projectId: string, path: string): Promise<SolutionFile>;
  createFile(ownerId: string, projectId: string, path: string): Promise<SolutionProject>;
  uploadFiles(
    ownerId: string,
    projectId: string,
    files: readonly UploadedFile[],
  ): Promise<SolutionProject>;
  saveFile(
    ownerId: string,
    projectId: string,
    path: string,
    content: string,
    expectedSha: string,
  ): Promise<SolutionProject>;
  deleteFile(
    ownerId: string,
    projectId: string,
    path: string,
    expectedSha: string,
  ): Promise<SolutionProject>;
  submit(ownerId: string, projectId: string, expectedCommitSha: string): Promise<SolutionProject>;
  getSnapshot(ownerId: string, projectId: string): Promise<SubmissionSnapshot | null>;
}

export class SolutionError extends Error {
  constructor(
    readonly code: 'not_found' | 'conflict' | 'read_only' | 'validation',
    message: string,
  ) {
    super(message);
  }
}
