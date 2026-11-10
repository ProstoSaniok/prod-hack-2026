import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';
import { provideRouter, withInMemoryScrolling } from '@angular/router';
import { provideEventPlugins } from '@taiga-ui/event-plugins';

import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';

bootstrapApplication(AppComponent, {
  providers: [
    provideHttpClient(),
    provideAnimations(),
    provideEventPlugins(),
    provideRouter(routes, withInMemoryScrolling({ anchorScrolling: 'enabled' })),
  ],
}).catch((error: unknown) => console.error(error));
