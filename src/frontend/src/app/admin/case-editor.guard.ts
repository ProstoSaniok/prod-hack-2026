export interface CaseEditorState {
  hasUnsavedChanges(): boolean;
}

export { leavePageGuard as caseEditorGuard } from '../shared/leave-page.service';
