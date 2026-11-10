import { DOCUMENT } from '@angular/common';
import { DestroyRef, Injectable, inject } from '@angular/core';
import { CanDeactivateFn } from '@angular/router';

type LeaveCheck = () => boolean | Promise<boolean>;

/** One departure decision for navigation and logout, before session revocation. */
@Injectable({ providedIn: 'root' })
export class LeavePageService {
  private readonly checks = new Map<LeaveCheck, () => boolean>();
  private pending: Promise<boolean> | null = null;
  private navigationApproved = false;

  constructor() {
    const window = inject(DOCUMENT).defaultView;
    const beforeUnload = (event: BeforeUnloadEvent): void => {
      if ([...this.checks.values()].some(dirty => dirty())) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window?.addEventListener('beforeunload', beforeUnload);
    inject(DestroyRef).onDestroy(() => window?.removeEventListener('beforeunload', beforeUnload));
  }

  register(check: LeaveCheck, dirty: () => boolean = () => false): () => void {
    this.checks.set(check, dirty);
    return () => this.checks.delete(check);
  }

  async confirmLeave(): Promise<boolean> {
    if (this.navigationApproved) {
      this.navigationApproved = false;
      return true;
    }
    if (this.pending) return this.pending;
    this.pending = this.checkAll();
    try {
      return await this.pending;
    } finally {
      this.pending = null;
    }
  }

  approveNextNavigation(): void {
    this.navigationApproved = true;
  }

  clearApproval(): void {
    this.navigationApproved = false;
  }

  private async checkAll(): Promise<boolean> {
    for (const check of this.checks.keys()) {
      if (!(await check())) return false;
    }
    return true;
  }
}

export const leavePageGuard: CanDeactivateFn<unknown> = () =>
  inject(LeavePageService).confirmLeave();
