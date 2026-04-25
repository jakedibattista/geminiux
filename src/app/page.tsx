export default function Home() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background text-foreground px-6">
      <main className="flex flex-col items-center justify-center w-full max-w-2xl flex-1 text-center space-y-6">
        <h1 className="text-5xl font-bold tracking-tight sm:text-6xl">
          AuditMySite
        </h1>

        <p className="text-xl text-muted-foreground">
          This project has been sunset and is no longer actively running.
        </p>

        <div className="rounded-lg border border-border bg-card p-6 text-left space-y-4 w-full">
          <h2 className="text-lg font-semibold">What was AuditMySite?</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            AuditMySite used multiple AI personas powered by Gemini to run live
            UX audits against any public URL. It captured screenshots across
            desktop and mobile viewports, streamed findings in real time, and
            produced a scored executive report with a narrated slide
            presentation.
          </p>
          <p className="text-muted-foreground text-sm leading-relaxed">
            It was built for the{" "}
            <a
              href="https://ai.google.dev/"
              className="underline hover:text-foreground transition-colors"
              target="_blank"
              rel="noopener noreferrer"
            >
              Gemini Live Agent Challenge
            </a>{" "}
            (Track 3: UI Navigator).
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-3 pt-2">
          <a
            href="https://youtu.be/VstSF9ii0cU"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center rounded-md bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            Watch the Demo
          </a>
          <a
            href="https://github.com/jakedibattista/geminiux"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center rounded-md border border-border px-5 py-2.5 text-sm font-medium hover:bg-accent transition-colors"
          >
            View Source on GitHub
          </a>
        </div>

        <p className="text-xs text-muted-foreground pt-4">
          The backend infrastructure (Cloud Run) has been decommissioned to
          eliminate ongoing hosting costs. The source code remains available on
          GitHub.
        </p>
      </main>
    </div>
  );
}
