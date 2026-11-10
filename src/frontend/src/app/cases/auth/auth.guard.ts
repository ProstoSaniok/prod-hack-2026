import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { BackendService } from '../../api/backend.service';

export function safeReturnUrl(value: string | null): string {
  if (value?.startsWith('/catalog?') && value.length <= 5000 && !value.includes('#')) {
    const query = new URLSearchParams(value.slice('/catalog?'.length));
    const keys = [...query.keys()];
    const ids = (query.get('competency_ids') ?? '').split(',').filter(Boolean);
    const offset = query.get('offset') ?? '0';
    if (
      keys.every(key => ['q', 'difficulty', 'competency_ids', 'offset'].includes(key)) &&
      new Set(keys).size === keys.length &&
      (query.get('q') ?? '').length <= 500 &&
      ['', 'performer', 'review_minus', 'review_plus', 'expert'].includes(
        query.get('difficulty') ?? '',
      ) &&
      ids.length <= 100 &&
      ids.every(id => /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(id)) &&
      /^\d+$/.test(offset) &&
      Number.isSafeInteger(Number(offset))
    )
      return '/catalog?' + query.toString();
    return '/catalog';
  }
  return value &&
    /^\/(?:admin(?:\/new|\/[a-z0-9-]+\/edit)?(?:\?section=(?:details|materials|reference))?|profile(?:#(?:wallet|history|activity|priority|skills|applications))?|catalog|my-cases(?:\/completed)?|(?:reviewer-applications|review-results|submissions)\/[a-z0-9-]+|reviews\/[a-z0-9-]+\/correction\/[a-z0-9-]+|(?:expert|demo-results|reviews)(?:\/[a-z0-9-]+)?|cases\/[a-z0-9-]+\/work|projects\/[a-f0-9-]+)$/.test(
      value,
    )
    ? value
    : '/catalog';
}

export const apiAuthGuard: CanActivateFn = async (_route, state) => {
  const api = inject(BackendService);
  const router = inject(Router);
  await api.initialized;
  if (/^\/admin(?:\/|$)/.test(state.url.split('?')[0] ?? '')) {
    try {
      await api.ensureAdminAccess();
      if (_route.queryParamMap.has('localDemo')) return router.createUrlTree(['/admin']);
      return true;
    } catch {
      return router.createUrlTree(['/login'], {
        queryParams: { returnUrl: safeReturnUrl(state.url) },
      });
    }
  }
  return api.user()
    ? true
    : router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
};

export const reviewerGuard: CanActivateFn = async () => {
  const api = inject(BackendService);
  const router = inject(Router);
  await api.initialized;
  try {
    await api.currentUser();
  } catch {
    return api.user()
      ? router.createUrlTree(['/catalog'], { queryParams: { reviewAccess: 'unavailable' } })
      : router.createUrlTree(['/login']);
  }
  if (!api.user()) return router.createUrlTree(['/login']);
  if (api.isOrganizer() && !api.hasReviewAssignments()) return router.createUrlTree(['/admin']);
  return api.canOpenReviews()
    ? true
    : router.createUrlTree(['/catalog'], {
        queryParams: { reviewAccess: api.reviewAccessError() ? 'unavailable' : 'denied' },
      });
};

export const participantGuard: CanActivateFn = async () => {
  const api = inject(BackendService);
  const router = inject(Router);
  await api.initialized;
  if (api.user()) {
    try {
      await api.currentUser();
    } catch {
      return router.createUrlTree(['/login']);
    }
  }
  return api.isOrganizer() ? router.createUrlTree(['/admin']) : true;
};

export const expertGuard: CanActivateFn = async (_route, state) => {
  const api = inject(BackendService);
  const router = inject(Router);
  await api.initialized;
  try {
    await api.currentUser();
  } catch {
    return api.user()
      ? router.createUrlTree(['/catalog'], { queryParams: { reviewAccess: 'unavailable' } })
      : router.createUrlTree(['/login'], { queryParams: { returnUrl: safeReturnUrl(state.url) } });
  }
  if (!api.user())
    return router.createUrlTree(['/login'], {
      queryParams: { returnUrl: safeReturnUrl(state.url) },
    });
  const path = state.url.split(/[?#]/)[0];
  if (path === '/expert/applications' ? api.canAssessReadiness() : api.canOpenQualityReviews())
    return true;
  if (api.isOrganizer() && !api.hasReviewAssignments()) return router.createUrlTree(['/admin']);
  return router.createUrlTree(['/catalog'], {
    queryParams: { reviewAccess: api.reviewAccessError() ? 'unavailable' : 'denied' },
  });
};
