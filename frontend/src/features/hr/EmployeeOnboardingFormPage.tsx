import { PageHeading, PageSection, PageShell } from '../../components/ui';
import { useSetCrumbs } from '../../context/CrumbsContext';

/**
 * Placeholder route for employee onboarding. The documents commit wired this
 * path in App.tsx before the form surface landed; keep the module so Vite
 * resolves the lazy import.
 */
export function EmployeeOnboardingFormPage() {
  useSetCrumbs([{ label: 'Employee onboarding' }]);

  return (
    <PageShell>
      <PageSection>
        <PageHeading>Employee onboarding</PageHeading>
        <p className="mt-3 text-sm text-[var(--text-subtle)]">
          This form is not available yet.
        </p>
      </PageSection>
    </PageShell>
  );
}
