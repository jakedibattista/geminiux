import Link from 'next/link';
import { Button } from '@/components/ui/button';

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background text-foreground">
      <main className="flex flex-col items-center justify-center w-full flex-1 px-20 text-center space-y-8">
        <h1 className="text-6xl font-bold">
          Audit My Site with <span className="text-primary">Gemini</span>
        </h1>
        
        <p className="mt-3 text-2xl text-muted-foreground max-w-2xl">
          Real Feedback from Almost Real Users
        </p>

        <div className="flex gap-4 mt-8">
          <Link href="/login">
            <Button size="lg" variant="outline">Sign In</Button>
          </Link>
          <Link href="/signup">
            <Button size="lg">Get Started</Button>
          </Link>
        </div>
      </main>
    </div>
  );
}