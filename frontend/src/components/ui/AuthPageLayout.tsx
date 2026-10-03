import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { Stack, Surface, Text } from '../../ui';
import { Logo } from './Logo';

export const AUTH_FIELD_INPUT_CLASSES = 'auth-field-input';

export const AUTH_FIELD_LABEL_CLASSES = 'auth-field-label';

export function AuthPanelHeading({
  children,
  id,
}: {
  children: ReactNode;
  id?: string;
}) {
  return (
    <h2 id={id} className="auth-panel-heading">
      <span className="auth-panel-heading__marker" aria-hidden="true" />
      <Text variant="display-sm" as="span">
        {children}
      </Text>
    </h2>
  );
}

interface AuthPageLayoutProps {
  title: ReactNode;
  description: ReactNode;
  children: ReactNode;
}

export function AuthPageLayout({
  title,
  description,
  children,
}: AuthPageLayoutProps) {
  const { key: routeKey } = useLocation();
  const [animationCycle, setAnimationCycle] = useState(0);

  useLayoutEffect(() => {
    // Force a fresh SVG animation timeline for each router location. Auth
    // routes can be restored from the route cache with an identical subtree.
    setAnimationCycle(cycle => cycle + 1);
  }, [routeKey]);

  useEffect(() => {
    // A BFCache restore resumes the old document and can leave CSS animations
    // frozen at their pagehide frame. Replacing the SVG restarts every wave.
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        setAnimationCycle(cycle => cycle + 1);
      }
    };

    window.addEventListener('pageshow', handlePageShow);
    return () => window.removeEventListener('pageshow', handlePageShow);
  }, []);

  return (
    <main className="auth-page relative isolate flex min-h-screen items-center justify-center overflow-hidden bg-[var(--section-bg)] px-5 py-10 sm:px-8">
      <span className="auth-pattern" aria-hidden="true">
        <svg
          key={animationCycle}
          className="auth-ripple auth-ripple--auth"
          viewBox="0 0 100 100"
          data-animation-cycle={animationCycle}
          aria-hidden="true"
        >
          <rect
            className="auth-ripple-square auth-ripple-square--frame"
            x="12"
            y="12"
            width="76"
            height="76"
            rx="13"
          />
          {Array.from({ length: 3 }, (_, index) => (
            <rect
              key={index}
              className="auth-ripple-square"
              x="12"
              y="12"
              width="76"
              height="76"
              rx="13"
            />
          ))}
        </svg>
      </span>

      <div className="auth-content-grid relative z-10 grid w-full max-w-[1120px] items-center gap-8 md:grid-cols-[minmax(0,1fr)_440px] lg:gap-12">
        <section className="flex w-full flex-col items-center text-center md:static md:self-stretch">
          <Stack gap="lg" align="center" className="auth-hero-copy w-full max-w-[620px]">
            <Text variant="display-md" as="h1" className="text-center">
              {title}
            </Text>
            <Text variant="body-lg" tone="muted" as="p" className="max-w-[560px] text-center">
              {description}
            </Text>
          </Stack>
        </section>

        <Surface
          as="section"
          tone="panel"
          border="default"
          radius="card"
          elevation="pop"
          className="w-full max-w-[440px] justify-self-center"
        >
          <div className="px-6 py-7 sm:px-9 sm:py-9">
          <div className="mb-7 flex justify-center">
            <Logo to="/" size="lg" />
          </div>
          {children}
          </div>
        </Surface>
      </div>
    </main>
  );
}
