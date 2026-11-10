import { Routes } from '@angular/router';
import { caseEditorGuard } from './admin/case-editor.guard';
import { projectEditorGuard } from './api/project-editor.guard';
import {
  apiAuthGuard,
  expertGuard,
  participantGuard,
  reviewerGuard,
} from './cases/auth/auth.guard';
import { leavePageGuard } from './shared/leave-page.service';

export const routes: Routes = [
  {
    path: '',
    redirectTo: 'register',
    pathMatch: 'full',
  },
  {
    path: 'admin',
    canActivate: [apiAuthGuard],
    loadComponent: () => import('./admin/admin-shell.component').then(m => m.AdminShellComponent),
    children: [
      {
        path: '',
        title: 'Кейсы',
        loadComponent: () => import('./admin/admin-page.component').then(m => m.AdminPageComponent),
      },
      {
        matcher: segments => {
          if (segments.length === 1 && segments[0]?.path === 'new') return { consumed: segments };
          if (segments.length === 2 && segments[1]?.path === 'edit' && segments[0])
            return { consumed: segments, posParams: { id: segments[0] } };
          return null;
        },
        title: 'Редактор кейса',
        canDeactivate: [caseEditorGuard],
        loadComponent: () =>
          import('./admin/case-editor-page.component').then(m => m.CaseEditorPageComponent),
      },
    ],
  },
  {
    path: 'ui-kit',
    loadComponent: () =>
      import('./ui-kit/ui-kit.component').then(({ UiKitComponent }) => UiKitComponent),
  },
  {
    path: 'sidebar',
    loadComponent: () =>
      import('./sidebar-page/sidebar-page.component').then(
        ({ SidebarPageComponent }) => SidebarPageComponent,
      ),
  },
  {
    path: '',
    loadComponent: () =>
      import('./cases/auth/auth-shell.component').then(m => m.AuthShellComponent),
    children: [
      {
        path: 'register',
        title: 'Регистрация',
        data: { mode: 'register' },
        loadComponent: () =>
          import('./cases/auth/auth-page.component').then(m => m.AuthPageComponent),
      },
      {
        path: 'login',
        title: 'Вход',
        data: { mode: 'login' },
        loadComponent: () =>
          import('./cases/auth/auth-page.component').then(m => m.AuthPageComponent),
      },
    ],
  },
  {
    path: '',
    loadComponent: () =>
      import('./cases/shell/case-shell.component').then(m => m.CaseShellComponent),
    children: [
      {
        path: 'expert',
        title: 'Экспертное ревью',
        canActivate: [apiAuthGuard, expertGuard],
        canDeactivate: [leavePageGuard],
        loadComponent: () => import('./api/reviews-api.component').then(m => m.ReviewsApiComponent),
      },
      {
        path: 'expert/applications',
        title: 'Заявки на эксперта',
        data: { applications: 'expert' },
        canActivate: [apiAuthGuard, expertGuard],
        loadComponent: () =>
          import('./api/reviewer-applications.component').then(
            m => m.ReviewerApplicationsComponent,
          ),
      },
      {
        path: 'reviewer-applications/:id',
        title: 'Заявка на эксперта',
        // The resource API checks applicant ownership or expert rights for its skill.
        canActivate: [apiAuthGuard],
        canDeactivate: [leavePageGuard],
        loadComponent: () =>
          import('./api/reviewer-application.component').then(m => m.ReviewerApplicationComponent),
      },
      {
        path: 'expert/:id',
        title: 'Проверка экспертом',
        // Owned resources remain readable after queue rights are revoked.
        // The resource API checks ownership and access to protected materials.
        canActivate: [apiAuthGuard],
        canDeactivate: [leavePageGuard],
        loadComponent: () => import('./api/reviews-api.component').then(m => m.ReviewsApiComponent),
      },
      {
        path: 'demo-results',
        redirectTo: 'my-cases/completed',
        pathMatch: 'full',
      },
      {
        path: 'demo-results/:id',
        redirectTo: 'my-cases/completed',
      },
      {
        path: 'profile',
        title: 'Профиль',
        canActivate: [apiAuthGuard, participantGuard],
        loadComponent: () => import('./api/profile.component').then(m => m.ProfileComponent),
      },
      {
        path: 'catalog',
        canActivate: [participantGuard],
        title: 'Каталог кейсов',
        data: { section: 'catalog' },
        loadComponent: () =>
          import('./api/learning-catalog.component').then(m => m.LearningCatalogComponent),
      },
      {
        path: 'my-cases/completed',
        title: 'Отправленные решения',
        data: { section: 'completed' },
        canActivate: [apiAuthGuard, participantGuard],
        loadComponent: () =>
          import('./api/learning-catalog.component').then(m => m.LearningCatalogComponent),
      },
      {
        path: 'reviews',
        title: 'Ревью',

        canActivate: [apiAuthGuard, reviewerGuard],
        canDeactivate: [leavePageGuard],
        loadComponent: () => import('./api/reviews-api.component').then(m => m.ReviewsApiComponent),
      },
      {
        path: 'review-results/:id',
        title: 'Результат ревью',
        data: { published: true },
        canActivate: [apiAuthGuard],
        loadComponent: () => import('./api/reviews-api.component').then(m => m.ReviewsApiComponent),
      },
      {
        path: 'reviews/:id/correction/:reviewId',
        title: 'Исправление ревью',
        canActivate: [apiAuthGuard],
        canDeactivate: [leavePageGuard],
        loadComponent: () => import('./api/reviews-api.component').then(m => m.ReviewsApiComponent),
      },
      {
        path: 'reviews/:id',
        title: 'Ревью решения',
        canActivate: [apiAuthGuard],
        canDeactivate: [leavePageGuard],
        loadComponent: () => import('./api/reviews-api.component').then(m => m.ReviewsApiComponent),
      },
      {
        path: 'my-cases',
        title: 'В работе',
        data: { section: 'started' },
        canActivate: [apiAuthGuard, participantGuard],
        loadComponent: () =>
          import('./api/learning-catalog.component').then(m => m.LearningCatalogComponent),
      },
      {
        path: 'submissions/:id',
        title: 'Отправленное решение',
        canActivate: [apiAuthGuard, participantGuard],
        loadComponent: () => import('./api/submission.component').then(m => m.SubmissionComponent),
      },
      {
        path: 'projects/:id',
        title: 'Работа над кейсом',
        canActivate: [apiAuthGuard, participantGuard],
        canDeactivate: [projectEditorGuard],
        data: { section: 'started' },
        loadComponent: () =>
          import('./api/learning-project.component').then(m => m.LearningProjectComponent),
      },
      {
        path: 'cases/:id/work',
        title: 'Работа над кейсом',
        canActivate: [apiAuthGuard, participantGuard],
        canDeactivate: [projectEditorGuard],
        data: { startCase: true, section: 'started' },
        loadComponent: () =>
          import('./api/learning-project.component').then(m => m.LearningProjectComponent),
      },
      {
        path: 'cases/:id',
        canActivate: [participantGuard],
        title: 'Условия кейса',
        loadComponent: () =>
          import('./api/learning-catalog.component').then(m => m.LearningCatalogComponent),
      },
    ],
  },
  {
    path: '**',
    redirectTo: '',
  },
];
