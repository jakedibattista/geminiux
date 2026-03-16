// Force all protected routes to be rendered dynamically at request time.
// These pages depend on Firebase Auth session cookies and client-side Firebase SDK,
// so they must never be statically pre-rendered at build time.
export const dynamic = 'force-dynamic';

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
