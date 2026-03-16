'use client';

import { useEffect, useState, use, useMemo, useRef } from 'react';
import { doc, onSnapshot, collection } from 'firebase/firestore';
import { db, reconnectFirestoreNetwork } from '@/lib/firebase';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import Link from 'next/link';

type PresentationSlide = {
  id: string;
  title: string;
  eyebrow?: string;
  bodyLines: string[];
  narration: string;
  screenshotUrl?: string;
  pageUrl?: string;
  pageLabel?: string;
  personaName?: string;
  audioUrl?: string;
  audioStoragePath?: string;
  audioMimeType?: string;
  visualSource?: 'evidence' | 'generated' | 'none';
  visualStoragePath?: string;
};

type PresentationArtifact = {
  status?: 'generating' | 'ready' | 'error';
  title?: string;
  subtitle?: string;
  score?: number | string;
  voice?: string;
  model?: string;
  slides?: PresentationSlide[];
  error?: string | null;
};

type AuditData = {
  url: string;
  status: 'pending' | 'running' | 'completed' | 'error';
  selectedPersonaIds: string[];
  customPersonas?: { id: string, name: string, description: string, goals: string[], deviceType: string }[];
  createdAt: string;
  consolidatedReport?: {
    summary: string;
    score: number;
    criticalIssues: string[];
    recommendations: string[];
    positives: string[];
  };
  mediaArtifacts?: {
    presentation?: PresentationArtifact;
  };
  crawledPages?: { url: string; label: string; screenshots: string[] }[];
};

type Finding = string | {
  text: string;
  screenshotUrl?: string | null;
  x?: number;
  y?: number;
  pageUrl?: string;
  pageKey?: string;
  pageLabel?: string;
  evidenceBacked?: boolean;
  category?: string;
  sentiment?: 'positive' | 'negative';
};

type Coverage = {
  positiveFindings?: number;
  negativeFindings?: number;
  distinctPages?: number;
  categoriesSeen?: string[];
};

type ActionEvent = {
  actionName?: string;
  status?: 'pending' | 'completed' | 'blocked' | 'error';
  blockedReason?: string;
  result?: string;
  urlBefore?: string;
  urlAfter?: string;
  screenshotUrl?: string;
  findingCount?: number;
  category?: string;
  sentiment?: 'positive' | 'negative';
};

type PersonaReport = {
  id: string;
  personaName: string;
  status: 'running' | 'completed' | 'error';
  findings: Finding[];
  currentAction?: string;
  latestScreenshot?: string;
  latestScreenshotPage?: string;
  pageScreenshots?: Record<string, string>; // normalized page key → download URL
  findingsCount?: number;
  coverage?: Coverage;
  authStatus?: 'not_requested' | 'succeeded' | 'failed';
  authError?: string | null;
  lastActionEvent?: ActionEvent;
  screenshotReview?: {
    status: string;
    reviewedCount: number;
    approvedCount: number;
    rejectedCount: number;
    reviews: { url: string; approved: boolean; reason?: string; pageUrl?: string }[];
  };
};

function getFindingText(f: Finding): string {
  return typeof f === 'string' ? f : f.text;
}
function getFindingScreenshot(f: Finding): string | null {
  return typeof f === 'string' ? null : (f.screenshotUrl ?? null);
}
function getFindingPageUrl(f: Finding): string | null {
  return typeof f === 'string' ? null : (f.pageUrl ?? null);
}
function getFindingPageKey(f: Finding): string | null {
  return typeof f === 'string' ? null : (f.pageKey ?? normalizePageKey(f.pageUrl));
}

function normalizePageKey(pageUrl?: string | null): string | null {
  if (!pageUrl) return null;

  try {
    const url = new URL(pageUrl);
    const path = url.pathname.replace(/\/+$/, '') || '/';
    return `${url.protocol}//${url.host}${path}`;
  } catch {
    return pageUrl;
  }
}

function isImageLikeUrl(url?: string | null): boolean {
  if (!url) return false;
  const cleaned = url.trim().toLowerCase();
  if (!cleaned) return false;
  if (cleaned.startsWith('data:image/')) return true;
  try {
    const parsed = new URL(cleaned);
    const path = decodeURIComponent(parsed.pathname || '').toLowerCase();
    return ['.png', '.jpg', '.jpeg', '.webp', '.gif'].some(ext => path.includes(ext));
  } catch {
    return false;
  }
}

function formatCategory(category?: string): string {
  if (!category) return 'Unknown';
  return category.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
}

function formatAuthStatus(status?: PersonaReport['authStatus']): string {
  switch (status) {
    case 'succeeded':
      return 'Signed in';
    case 'failed':
      return 'Sign-in issue';
    case 'not_requested':
      return 'No sign-in needed';
    default:
      return 'Unknown';
  }
}

function formatActionLabel(event?: ActionEvent): string {
  if (!event) return 'No recent action';

  const actionName = event.actionName || 'Action';
  const status = event.status || 'unknown';

  if (status === 'blocked') return `${actionName} was blocked`;
  if (status === 'error') return `${actionName} hit an error`;
  if (status === 'completed') return `${actionName} completed`;
  return actionName;
}

function getPersonaStatus(report?: PersonaReport): 'running' | 'completed' | 'error' | 'waiting' {
  if (!report?.status) return report ? 'running' : 'waiting';
  if (report.status === 'completed' || report.status === 'error' || report.status === 'running') {
    return report.status;
  }
  return 'running';
}

function getPersonaProgress(report?: PersonaReport): number {
  const status = getPersonaStatus(report);
  if (status === 'waiting' || !report) return 0.05;
  if (status === 'completed') return 1;
  if (status === 'error') return 0.98;

  const findings = Math.min((report.findingsCount || 0) / 3, 1);
  const positive = Math.min((report.coverage?.positiveFindings || 0) / 1, 1);
  const negative = Math.min((report.coverage?.negativeFindings || 0) / 2, 1);
  const coverageAreas = Math.min((report.coverage?.distinctPages || 0) / 2, 1);
  const categories = Math.min((report.coverage?.categoriesSeen?.length || 0) / 2, 1);
  const hasStartedWork = report.currentAction || report.lastActionEvent || (report.findingsCount || 0) > 0;

  return Math.min(
    0.12 +
      (hasStartedWork ? 0.08 : 0) +
      findings * 0.35 +
      positive * 0.12 +
      negative * 0.14 +
      coverageAreas * 0.11 +
      categories * 0.08,
    0.92
  );
}

/**
 * Renders an attributed issue/recommendation in the format:
 *   "Title (Persona A, Persona B): Description of the problem."
 * The title and personas are bolded/badged; description is normal weight.
 */
function AttributedItem({ text }: { text: string }) {
  // Match: "Title (Persona list): rest of text"
  const match = text.match(/^(.+?)\s*\(([^)]+)\)\s*:\s*([\s\S]+)$/);
  if (!match) {
    return <li className="text-sm">{text}</li>;
  }
  const [, title, personasStr, description] = match;
  const personas = personasStr.split(',').map(p => p.trim());

  return (
    <li className="text-sm space-y-1">
      <div className="flex flex-wrap items-baseline gap-1.5">
        <span className="font-semibold">{title}</span>
        {personas.map(p => (
          <span key={p} className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {p}
          </span>
        ))}
      </div>
      <p className="text-muted-foreground leading-relaxed">{description}</p>
    </li>
  );
}

function buildAgentPrompt(
  audit: AuditData,
  personaReports: PersonaReport[],
  personaDisplayNames: Record<string, string>
): string {
  const r = audit.consolidatedReport!;
  const lines: string[] = [];

  lines.push(`# UX Audit of ${getFriendlySiteName(audit.url)} — ${audit.url}`);
  lines.push(`**Overall UX Score: ${r.score}/100**`);
  lines.push('');
  lines.push('I ran an automated multi-persona UX audit on the site above. Below are the full findings from each AI persona plus a consolidated executive report. Please help me address the issues.');
  lines.push('');
  lines.push('---');
  lines.push('');
  lines.push('## Executive Summary');
  lines.push('');
  lines.push(r.summary);
  lines.push('');

  if (r.criticalIssues?.length) {
    lines.push('## Critical Issues');
    lines.push('');
    r.criticalIssues.forEach((issue, i) => lines.push(`${i + 1}. ${issue}`));
    lines.push('');
  }

  if (r.recommendations?.length) {
    lines.push('## Recommended Fixes');
    lines.push('');
    r.recommendations.forEach((rec, i) => lines.push(`${i + 1}. ${rec}`));
    lines.push('');
  }

  if (r.positives?.length) {
    lines.push('## What Worked Well (keep these)');
    lines.push('');
    r.positives.forEach((pos, i) => lines.push(`${i + 1}. ${pos}`));
    lines.push('');
  }

  if (personaReports.length > 0) {
    lines.push('---');
    lines.push('');
    lines.push('## Raw Findings by Persona');
    lines.push('');
    personaReports.forEach(report => {
      const name = personaDisplayNames[report.id] || report.personaName || report.id;
      lines.push(`### ${name}`);
      lines.push('');
      (report.findings || []).forEach(f => lines.push(`- ${getFindingText(f)}`));
      lines.push('');
    });
  }

  lines.push('---');
  lines.push('');
  lines.push('Please prioritise the critical issues above and suggest specific code changes or design decisions to fix them.');

  return lines.join('\n');
}

function formatVoiceName(voice?: string): string {
  if (!voice) return 'Default';
  return voice.charAt(0).toUpperCase() + voice.slice(1);
}

function formatPageLabel(pageUrl?: string): string | null {
  if (!pageUrl) return null;

  try {
    const url = new URL(pageUrl);
    const hostname = url.hostname.replace(/^www\./, '');
    const path = url.pathname === '/' ? 'Homepage' : url.pathname.replace(/^\/+/, '').split('/').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
    return path === 'Homepage' ? `${hostname} Homepage` : path;
  } catch {
    return pageUrl;
  }
}

function CopyReportDialog({
  audit,
  personaReports,
  personaDisplayNames,
}: {
  audit: AuditData;
  personaReports: PersonaReport[];
  personaDisplayNames: Record<string, string>;
}) {
  const [copied, setCopied] = useState(false);
  const prompt = buildAgentPrompt(audit, personaReports, personaDisplayNames);

  function handleCopy() {
    navigator.clipboard.writeText(prompt).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          Send to Agent
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Copy Report for Your Coding Agent</DialogTitle>
          <p className="text-sm text-muted-foreground">
            Paste this into Cursor, Claude Code, or any AI assistant to get specific fix suggestions.
          </p>
        </DialogHeader>
        <div className="flex-1 overflow-hidden flex flex-col gap-3 min-h-0">
          <pre className="flex-1 overflow-y-auto rounded-md border bg-muted/50 p-4 text-xs leading-relaxed whitespace-pre-wrap font-mono">
            {prompt}
          </pre>
          <Button onClick={handleCopy} className="w-full gap-2 shrink-0">
            {copied ? (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                Copied!
              </>
            ) : (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
                </svg>
                Copy to Clipboard
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function PresentationTab({ presentation, auditUrl }: { presentation?: PresentationArtifact; auditUrl?: string }) {
  const slides = presentation?.slides || [];
  const [currentSlideIndex, setCurrentSlideIndex] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);

  const defaultTitle = `UX Audit of ${getFriendlySiteName(auditUrl || presentation?.title || '')}`;

  const safeIndex = Math.min(currentSlideIndex, slides.length - 1);
  const slide = slides[safeIndex];
  const slideImageUrl = isImageLikeUrl(slide?.screenshotUrl) ? slide.screenshotUrl! : null;

  // Auto-play audio when slide changes
  useEffect(() => {
    if (audioRef.current && slide?.audioUrl) {
      audioRef.current.load(); // Ensure the new source is loaded
      audioRef.current.play().catch(err => {
        // Autoplay may be blocked if there's no user interaction yet
        console.warn('Autoplay prevented:', err);
      });
    }
  }, [safeIndex, slide?.audioUrl]);

  if (!presentation) {
    return null;
  }

  if (presentation.status === 'generating') {
    return (
      <div className="mt-6">
        <Card>
          <CardHeader>
            <CardTitle>{presentation.title || defaultTitle}</CardTitle>
            <CardDescription>
              Building your guided presentation with voiceover and grounded screenshots.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              The presentation is generating in the background and will appear here automatically when it is ready.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (presentation.status === 'error') {
    return (
      <div className="mt-6">
        <Card>
          <CardHeader>
            <CardTitle>{presentation.title || defaultTitle}</CardTitle>
            <CardDescription>We hit a problem while generating the guided presentation.</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-destructive">{presentation.error || 'Presentation generation failed.'}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!slides.length) {
    return (
      <div className="mt-6">
        <Card>
          <CardHeader>
            <CardTitle>{presentation.title || defaultTitle}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">No slides are available yet.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mt-6 space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{presentation.title || defaultTitle}</CardTitle>
          <CardDescription>
            {presentation.subtitle || 'A guided walkthrough of the audit with voice narration and grounded evidence.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge>{`Slide ${safeIndex + 1} of ${slides.length}`}</Badge>
            {presentation.voice && (
              <Badge variant="outline">Voice: {formatVoiceName(presentation.voice)}</Badge>
            )}
            {presentation.score !== undefined && (
              <Badge variant="outline">UX Score: {presentation.score}</Badge>
            )}
          </div>
          <Progress value={((safeIndex + 1) / slides.length) * 100} className="h-2" />
          <div className="flex flex-wrap gap-2">
            {slides.map((slideOption, index) => (
              <Button
                key={slideOption.id}
                variant={index === safeIndex ? 'default' : 'outline'}
                size="sm"
                onClick={() => setCurrentSlideIndex(index)}
              >
                {index + 1}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden">
        <CardHeader className="space-y-3">
          {slide.eyebrow && (
            <div className="text-xs uppercase tracking-wide text-muted-foreground">{slide.eyebrow}</div>
          )}
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="space-y-2">
              <CardTitle className="text-2xl">{slide.title}</CardTitle>
              <div className="flex flex-wrap gap-2">
                {slide.personaName && <Badge variant="secondary">{slide.personaName}</Badge>}
                {slide.pageLabel && <Badge variant="outline" className="max-w-full truncate">{slide.pageLabel}</Badge>}
                {!slide.pageLabel && formatPageLabel(slide.pageUrl) && <Badge variant="outline" className="max-w-full truncate">{formatPageLabel(slide.pageUrl)}</Badge>}
              </div>
            </div>
            <div className="w-full md:w-[360px]">
              {slide.audioUrl ? (
                <audio ref={audioRef} controls className="w-full" src={slide.audioUrl}>
                  Your browser does not support audio playback.
                </audio>
              ) : (
                <p className="text-sm text-muted-foreground">Audio is still loading for this slide.</p>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-6 lg:grid-cols-[1.25fr_0.75fr]">
          <div className="space-y-3">
            {slideImageUrl ? (
              <a
                href={slideImageUrl}
                target="_blank"
                rel="noreferrer"
                className="block rounded-2xl border overflow-hidden bg-muted/20 shadow-sm"
              >
                <div className="flex items-center justify-center bg-muted/10 p-3">
                  <img
                    src={slideImageUrl}
                    alt={slide.title}
                    className="w-full h-auto max-h-[520px] object-contain object-top bg-white"
                  />
                </div>
              </a>
            ) : (
              <div className="rounded-2xl border min-h-[360px] bg-gradient-to-br from-primary/15 via-background to-primary/5 p-8 flex flex-col justify-between shadow-sm">
                <div className="space-y-3">
                  {slide.eyebrow && (
                    <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">{slide.eyebrow}</p>
                  )}
                  <h3 className="text-3xl font-semibold tracking-tight max-w-xl">{slide.title}</h3>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="h-16 rounded-xl bg-primary/15" />
                  <div className="h-16 rounded-xl bg-primary/10" />
                  <div className="h-16 rounded-xl bg-primary/20" />
                </div>
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              {slideImageUrl ? 'Grounded in a real screenshot captured during the audit.' : 'Visual summary generated from the audit findings.'}
            </p>
          </div>

          <div className="space-y-4 flex flex-col justify-center">
            <div className="rounded-2xl border bg-muted/15 p-5">
              <ul className="space-y-4">
                {slide.bodyLines.map((line, index) => (
                  <li key={`${slide.id}-${index}`} className="flex items-start gap-3">
                    <span className="mt-2 h-2.5 w-2.5 rounded-full bg-primary shrink-0" />
                    <p className="text-base leading-7 text-foreground">{line}</p>
                  </li>
                ))}
              </ul>
            </div>

            {!slide.bodyLines.length && (
              <p className="text-sm text-muted-foreground">This slide is still being prepared.</p>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between gap-3">
        <Button
          variant="outline"
          onClick={() => setCurrentSlideIndex(index => Math.max(0, index - 1))}
          disabled={safeIndex === 0}
        >
          Previous
        </Button>
        <Button
          onClick={() => setCurrentSlideIndex(index => Math.min(slides.length - 1, index + 1))}
          disabled={safeIndex === slides.length - 1}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

const PERSONA_DISPLAY_NAMES: Record<string, string> = {
  'p_first_time': 'First-Time Visitor',
  'p_mobile': 'Mobile User',
  'p_accessibility': 'Accessibility User',
  'p_non_technical': 'Non-Technical User',
  'p_power_user': 'Power User',
};

function getFriendlySiteName(url?: string): string {
  if (!url) return 'Site';
  try {
    const hostname = new URL(url).hostname.replace(/^www\./, '');
    const root = hostname.split('.')[0];
    const words = root.split(/[-_]+/).filter(Boolean);
    return words.map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ') || hostname;
  } catch {
    return url || 'Site';
  }
}

export default function AuditPage({ params }: { params: Promise<{ auditId: string }> }) {
  const { auditId } = use(params);
  const [audit, setAudit] = useState<AuditData | null>(null);
  const [personaReports, setPersonaReports] = useState<PersonaReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [awaitingPresentationInit, setAwaitingPresentationInit] = useState(false);
  const [activeTab, setActiveTab] = useState<'presentation' | 'consolidated' | 'screenshots' | 'live'>('screenshots');
  const [firestoreWarning, setFirestoreWarning] = useState<string | null>(null);
  const [isRetryingFirestore, setIsRetryingFirestore] = useState(false);
  const [isBrowserOnline, setIsBrowserOnline] = useState(true);
  // Simulated heartbeat progress — slowly creeps forward while agents are active
  // so the bar never sits at 0% during the long browsing phase.
  const [simulatedProgress, setSimulatedProgress] = useState(3);
  const auditStatus = audit?.status;
  const presentation = audit?.mediaArtifacts?.presentation;
  const presentationStatus = presentation?.status;
  const isPresentationPending =
    auditStatus === 'completed' &&
    (presentationStatus === 'generating' || awaitingPresentationInit);
  const isPresentationGenerating = isPresentationPending;
  const showFirestoreWarning = !isBrowserOnline || Boolean(firestoreWarning);

  async function handleRetryFirestore() {
    setIsRetryingFirestore(true);
    try {
      await reconnectFirestoreNetwork();
      setFirestoreWarning(null);
    } catch (error) {
      setFirestoreWarning(error instanceof Error ? error.message : 'Could not reconnect to Firestore yet.');
    } finally {
      setIsRetryingFirestore(false);
    }
  }

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const syncOnlineState = () => {
      const online = window.navigator.onLine;
      setIsBrowserOnline(online);
      if (online) {
        setFirestoreWarning(null);
      } else {
        setFirestoreWarning('Your browser is offline. Live audit updates will pause until Firestore reconnects.');
      }
    };

    syncOnlineState();
    window.addEventListener('online', syncOnlineState);
    window.addEventListener('offline', syncOnlineState);

    return () => {
      window.removeEventListener('online', syncOnlineState);
      window.removeEventListener('offline', syncOnlineState);
    };
  }, []);

  useEffect(() => {
    // 1. Listen to the main audit document
    const auditUnsubscribe = onSnapshot(
      doc(db, 'audits', auditId),
      (snapshot) => {
        setFirestoreWarning(null);
        if (snapshot.exists()) {
          const nextAudit = snapshot.data() as AuditData;
          setAudit((previousAudit) => {
            const previousStatus = previousAudit?.status;
            const nextPresentationStatus = nextAudit.mediaArtifacts?.presentation?.status;

            if (previousStatus === 'running' && nextAudit.status === 'completed' && !nextPresentationStatus) {
              setAwaitingPresentationInit(true);
            }

            if (nextPresentationStatus || nextAudit.status !== 'completed') {
              setAwaitingPresentationInit(false);
            }

            return nextAudit;
          });
        }
        setLoading(false);
      },
      (error) => {
        console.error("Error fetching audit:", error);
        setFirestoreWarning('Live audit updates lost connection to Firestore. You can retry the connection.');
        setLoading(false);
      }
    );

    // 2. Listen to the agentReports subcollection for real-time persona agent updates
    const reportsUnsubscribe = onSnapshot(
      collection(db, `audits/${auditId}/agentReports`),
      (snapshot) => {
        setFirestoreWarning(null);
        const reports = snapshot.docs.map(doc => ({
          id: doc.id,
          ...doc.data()
        })) as PersonaReport[];
        setPersonaReports(reports);
      },
      (error) => {
        console.error("Error fetching agent reports:", error);
        setFirestoreWarning('Live agent updates lost connection to Firestore. You can retry the connection.');
      }
    );

    return () => {
      auditUnsubscribe();
      reportsUnsubscribe();
    };
  }, [auditId]);

  // Keep a gentle floor under progress so the UI never feels frozen before
  // the agents start emitting meaningful state updates.
  useEffect(() => {
    if (!auditStatus) return;

    if (auditStatus === 'completed' && isPresentationGenerating) {
      const id = setInterval(() => {
        setSimulatedProgress(prev => {
          const remaining = 99 - prev;
          return prev + remaining * 0.12;
        });
      }, 1500);
      return () => clearInterval(id);
    }

    if (auditStatus !== 'running') return;

    const id = setInterval(() => {
      setSimulatedProgress(prev => {
        const remaining = 35 - prev;
        return prev + remaining * 0.08;
      });
    }, 2000);
    return () => clearInterval(id);
  }, [auditStatus, isPresentationGenerating]);

  useEffect(() => {
    const auditIsCompleted = auditStatus === 'completed';
    const presentationAvailable = Boolean(presentation || isPresentationPending);
    const availableTabs: Array<'presentation' | 'consolidated' | 'screenshots' | 'live'> = [
      ...(auditIsCompleted && presentationAvailable ? ['presentation' as const] : []),
      ...(auditIsCompleted ? ['consolidated' as const] : []),
      'screenshots',
      'live',
    ];

    setActiveTab((currentTab) => {
      if (availableTabs.includes(currentTab)) {
        return currentTab;
      }
      return auditIsCompleted
        ? (presentationAvailable ? 'presentation' : 'consolidated')
        : 'screenshots';
    });
  }, [auditStatus, presentation, isPresentationPending]);

  type PageGroup = {
    imgUrl: string;
    pageUrl: string;
    pageKey: string;
    isMobile: boolean;
    agents: { name: string; findings: Finding[] }[];
  };

  const screenshotGroups = useMemo(() => {
    // Build a map of screenshot URL → group details including all agents
    // who saw/referenced this specific screenshot.
    const pageMap = new Map<string, PageGroup>();

    if (!audit) return [];

    // 1. Initialize with all screenshots captured by the initial crawler
    if (audit.crawledPages) {
      audit.crawledPages.forEach(p => {
        // Use new dual-view fields if available, otherwise fallback to screenshots
        const desktopShots = (p as any).desktop_screenshots || p.screenshots || [];
        const mobileShots = (p as any).mobile_screenshots || [];

        desktopShots.forEach((imgUrl: string) => {
          if (!isImageLikeUrl(imgUrl)) return;
          const normalizedPageKey = normalizePageKey(p.url) || p.url;
          if (!pageMap.has(imgUrl)) {
            pageMap.set(imgUrl, {
              imgUrl,
              pageUrl: p.url,
              pageKey: normalizedPageKey,
              isMobile: false,
              agents: [],
            });
          }
        });

        mobileShots.forEach((imgUrl: string) => {
          if (!isImageLikeUrl(imgUrl)) return;
          const normalizedPageKey = normalizePageKey(p.url) || p.url;
          if (!pageMap.has(imgUrl)) {
            pageMap.set(imgUrl, {
              imgUrl,
              pageUrl: p.url,
              pageKey: normalizedPageKey,
              isMobile: true,
              agents: [],
            });
          }
        });
      });
    }

    // Build a reverse map from normalized pageKey → first available crawled screenshot URL.
    // This is used as a last-resort fallback when a finding's specific screenshot was rejected
    // by the screenshot reviewer and pageScreenshots was also filtered out.
    const crawledPageKeyToImgUrl = new Map<string, string>();
    if (audit.crawledPages) {
      audit.crawledPages.forEach(p => {
        const normalizedPageKey = normalizePageKey(p.url) || p.url;
        if (!crawledPageKeyToImgUrl.has(normalizedPageKey)) {
          const desktopShots = (p as any).desktop_screenshots || p.screenshots || [];
          const mobileShots = (p as any).mobile_screenshots || [];
          const firstShot = [...desktopShots, ...mobileShots].find((url: string) => isImageLikeUrl(url));
          if (firstShot) crawledPageKeyToImgUrl.set(normalizedPageKey, firstShot);
        }
      });
    }

    // 2. Add/enrich with persona-specific reports.
    // Exclude crawler agents — their latestScreenshot values are live-feed
    // shot_ artifacts that differ from the canonical composite URLs in
    // crawledPages and would otherwise create duplicate cards.
    personaReports.filter(r => !r.id.startsWith('crawler_')).forEach(report => {
      const name = PERSONA_DISPLAY_NAMES[report.id] ||
        audit.customPersonas?.find(p => p.id === report.id)?.name ||
        report.personaName || report.id;

      const isCustom = audit.customPersonas?.find(p => p.id === report.id);
      const agentIsMobile = isCustom
        ? isCustom.deviceType === 'mobile'
        : report.id === 'p_mobile';

      // 1. Collect all screenshots from the report's page screenshots map
      if (report.pageScreenshots) {
        Object.entries(report.pageScreenshots).forEach(([storedPageKey, imgUrl]) => {
          if (!isImageLikeUrl(imgUrl)) return;
          const normalizedPageKey = normalizePageKey(storedPageKey) || storedPageKey;
          
          const existing = pageMap.get(imgUrl);
          if (!existing) {
            pageMap.set(imgUrl, {
              imgUrl,
              pageUrl: storedPageKey,
              pageKey: normalizedPageKey,
              isMobile: agentIsMobile,
              agents: [],
            });
          } else if (!agentIsMobile && existing.isMobile) {
            // If a desktop agent uses a screenshot previously marked mobile,
            // it's likely a desktop screenshot used as fallback, so use desktop styling.
            existing.isMobile = false;
          }
        });
      }

      // 2. Also consider the latest screenshot
      if (isImageLikeUrl(report.latestScreenshot) && report.latestScreenshotPage) {
        const imgUrl = report.latestScreenshot;
        const existing = pageMap.get(imgUrl);
        if (!existing) {
          pageMap.set(imgUrl, {
            imgUrl,
            pageUrl: report.latestScreenshotPage,
            pageKey: normalizePageKey(report.latestScreenshotPage) || report.latestScreenshotPage,
            isMobile: agentIsMobile,
            agents: [],
          });
        } else if (!agentIsMobile && existing.isMobile) {
          existing.isMobile = false;
        }
      }

      // 3. Map findings to these screenshots
      (report.findings || []).forEach(finding => {
        const pageKey = getFindingPageKey(finding);
        if (!pageKey) return;

        const findingScreenshot = getFindingScreenshot(finding);
        const findingPageUrl = getFindingPageUrl(finding) || pageKey;
        const pageScreenshot = report.pageScreenshots && isImageLikeUrl(report.pageScreenshots[pageKey])
          ? report.pageScreenshots[pageKey]
          : null;
        const latestScreenshotForPage = report.latestScreenshotPage &&
          normalizePageKey(report.latestScreenshotPage) === pageKey &&
          isImageLikeUrl(report.latestScreenshot)
          ? report.latestScreenshot
          : null;
        
        // Use the finding's specific screenshot, or fallback to the one we have for this page.
        // If the screenshot reviewer rejected the finding's screenshot AND filtered it from
        // pageScreenshots, fall back to any crawled screenshot for the same page so the
        // finding's quote still appears on screen.
        const imgUrl = (isImageLikeUrl(findingScreenshot) ? findingScreenshot : null) || 
          pageScreenshot || 
          latestScreenshotForPage ||
          crawledPageKeyToImgUrl.get(pageKey) ||
          null;

        if (!imgUrl) return;

        // Ensure the screenshot is in our map
        const existing = pageMap.get(imgUrl);
        if (!existing) {
          pageMap.set(imgUrl, {
            imgUrl,
            pageUrl: findingPageUrl,
            pageKey,
            isMobile: agentIsMobile,
            agents: [],
          });
        } else if (!agentIsMobile && existing.isMobile) {
          existing.isMobile = false;
        }

        const group = pageMap.get(imgUrl)!;
        let agent = group.agents.find(a => a.name === name);
        if (!agent) {
          agent = { name, findings: [] };
          group.agents.push(agent);
        }
        agent.findings.push(finding);
      });
    });

    // Sort groups so that Desktop and Mobile for the same page appear together.
    return Array.from(pageMap.values()).sort((a, b) => {
      // 1. Group by Page URL (lexicographical)
      const urlCompare = (a.pageUrl || '').localeCompare(b.pageUrl || '');
      if (urlCompare !== 0) return urlCompare;
      
      // 2. Group by Device (Desktop first, then Mobile)
      if (a.isMobile !== b.isMobile) return a.isMobile ? 1 : -1;

      // 3. Sort by Timestamp (oldest to newest within a page/device to follow scroll order)
      const getTs = (url: string) => {
        const match = url.match(/(\d+)\.png/);
        return match ? parseInt(match[1], 10) : 0;
      };
      return getTs(a.imgUrl) - getTs(b.imgUrl);
    });
  }, [audit, personaReports]);

  if (loading) return <div className="p-8 text-center">Loading audit...</div>;
  if (!audit) return <div className="p-8 text-center">Audit not found.</div>;

  const isCompleted = audit.status === 'completed';
  const completedPersonas = personaReports.filter(r => r.status === 'completed').length;
  const totalPersonas = audit.selectedPersonaIds.length || 1;
  const allPersonasDone = completedPersonas >= totalPersonas;
  const showProgressBar = !isCompleted || isPresentationPending;
  const statusLabel = audit.status === 'error'
    ? 'ERROR'
    : isPresentationPending
    ? 'BUILDING PRESENTATION'
    : audit.status.toUpperCase();
  const statusVariant = audit.status === 'error'
    ? 'destructive'
    : isCompleted && !isPresentationPending
    ? 'default'
    : 'secondary';
  const personaProgressAverage = totalPersonas
    ? audit.selectedPersonaIds.reduce((sum, personaId) => {
        const report = personaReports.find(r => r.id === personaId);
        return sum + getPersonaProgress(report);
      }, 0) / totalPersonas
    : 0;

  // Personas account for most of the progress, and their live coverage state
  // drives the bar so it keeps moving as the agents actually make progress.
  const realProgress = isPresentationPending
    ? 96
    : isCompleted
    ? 100
    : allPersonasDone
    ? 95 // consolidator is running
    : Math.max((completedPersonas / totalPersonas) * 90, personaProgressAverage * 90);
  // Use whichever is higher so the bar never goes backward
  const progressPercent = showProgressBar
    ? Math.max(realProgress, simulatedProgress)
    : 100;

  return (
    <div className="container mx-auto p-8 max-w-6xl space-y-8">
      {showFirestoreWarning && (
        <Alert variant="destructive">
          <AlertTitle>Live updates are temporarily disconnected</AlertTitle>
          <AlertDescription>
            <p>
              {firestoreWarning || 'Cloud Firestore is offline right now, so live audit updates may pause until the connection recovers.'}
            </p>
            <div className="mt-3">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleRetryFirestore}
                disabled={isRetryingFirestore}
              >
                {isRetryingFirestore ? 'Reconnecting...' : 'Retry Firestore Connection'}
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Header Area */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <Link href="/dashboard" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-3">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m12 19-7-7 7-7"/><path d="M19 12H5"/></svg>
            New audit
          </Link>
          <h1 className="text-3xl font-bold mb-2">UX Audit of {getFriendlySiteName(audit.url)}</h1>
          <div className="flex items-center gap-3 text-muted-foreground mb-3">
            <a href={audit.url} target="_blank" rel="noreferrer" className="hover:text-primary transition-colors">
              {audit.url}
            </a>
            <span>•</span>
            <span>{new Date(audit.createdAt).toLocaleString()}</span>
          </div>
          {isCompleted && (
            <div className="flex flex-wrap gap-2">
              <CopyReportDialog
                audit={audit}
                personaReports={personaReports}
                personaDisplayNames={PERSONA_DISPLAY_NAMES}
              />
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 w-full md:w-auto">
          <div className="flex items-center gap-2">
            <Badge 
              variant={statusVariant}
              className="text-sm px-3 py-1"
            >
              {statusLabel}
            </Badge>
          </div>
          {showProgressBar && (
            <div className="w-full md:w-48 space-y-1">
              <div className="flex justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  {isPresentationGenerating ? (
                    <>
                      <span className="relative flex h-1.5 w-1.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-primary"></span>
                      </span>
                      Building presentation...
                    </>
                  ) : allPersonasDone ? (
                    <>
                      <span className="relative flex h-1.5 w-1.5">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-primary"></span>
                      </span>
                      Consolidating...
                    </>
                  ) : (
                    'Agent Progress'
                  )}
                </span>
                <span>{Math.round(progressPercent)}%</span>
              </div>
              <Progress value={progressPercent} className="h-2" />
            </div>
          )}
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as typeof activeTab)} className="w-full">
        <TabsList className="w-full justify-start border-b rounded-none h-auto bg-transparent p-0">
          {isCompleted && (presentation || isPresentationPending) && (
            <TabsTrigger 
              value="presentation" 
              onClick={() => setActiveTab('presentation')}
              className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
            >
              Presentation
            </TabsTrigger>
          )}
          {isCompleted && (
            <TabsTrigger 
              value="consolidated" 
              onClick={() => setActiveTab('consolidated')}
              className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
            >
              Consolidated Report
            </TabsTrigger>
          )}
          <TabsTrigger 
            value="screenshots" 
            onClick={() => setActiveTab('screenshots')}
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            Screenshots
          </TabsTrigger>
          <TabsTrigger 
            value="live" 
            onClick={() => setActiveTab('live')}
            className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent"
          >
            Live Agent Feeds
          </TabsTrigger>
        </TabsList>

        {isCompleted && (
          <TabsContent value="presentation">
            <PresentationTab 
              presentation={presentationStatus ? presentation : {
                status: 'generating',
                title: `UX Audit of ${getFriendlySiteName(audit.url)}`,
                subtitle: 'Building your guided presentation with voiceover and grounded screenshots.',
              }} 
              auditUrl={audit.url}
            />
          </TabsContent>
        )}

        {isCompleted && (
          <TabsContent value="consolidated" className="mt-6 space-y-6">
            <div className="grid md:grid-cols-3 gap-6">
              <Card className="md:col-span-2">
                <CardHeader>
                  <CardTitle>Executive Summary</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-muted-foreground whitespace-pre-wrap">
                    {audit.consolidatedReport?.summary || "Summary generation failed."}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>UX Score</CardTitle>
                </CardHeader>
                <CardContent className="flex items-center justify-center h-32">
                  <span className="text-6xl font-bold text-primary">
                    {audit.consolidatedReport?.score || "N/A"}
                  </span>
                  <span className="text-xl text-muted-foreground ml-2">/ 100</span>
                </CardContent>
              </Card>
            </div>

            <div className="grid md:grid-cols-2 gap-6">
              <Card>
                <CardHeader>
                  <CardTitle>Critical Issues</CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-3">
                    {audit.consolidatedReport?.criticalIssues?.map((issue, i) => (
                      <AttributedItem key={i} text={issue} />
                    )) || <li className="text-sm text-muted-foreground">No critical issues found!</li>}
                  </ul>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Key Recommendations</CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-3">
                    {audit.consolidatedReport?.recommendations?.map((rec, i) => (
                      <AttributedItem key={i} text={rec} />
                    )) || <li className="text-sm text-muted-foreground">No recommendations provided.</li>}
                  </ul>
                </CardContent>
              </Card>
            </div>

            {audit.consolidatedReport?.positives && audit.consolidatedReport.positives.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>What Worked Well</CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-3">
                    {audit.consolidatedReport.positives.map((item, i) => (
                      <AttributedItem key={i} text={item} />
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}

          </TabsContent>
        )}

        <TabsContent value="live" className="mt-6">
          <div className="grid lg:grid-cols-2 gap-6">
            {audit.selectedPersonaIds.map((personaId) => {
              const report = personaReports.find(r => r.id === personaId);
              const reportStatus = getPersonaStatus(report);
              
              return (
                <Card key={personaId} className="flex flex-col">
                  <CardHeader className="border-b bg-muted/20 pb-4">
                    <div className="flex justify-between items-center">
                      <CardTitle className="text-lg">
                        {PERSONA_DISPLAY_NAMES[personaId] ||
                         audit.customPersonas?.find(p => p.id === personaId)?.name ||
                         report?.personaName ||
                         personaId.replace('p_', '').replace('_', ' ').toUpperCase()}
                      </CardTitle>
                      <Badge variant={
                        reportStatus === 'waiting' ? 'outline' :
                        reportStatus === 'completed' ? 'default' :
                        reportStatus === 'error' ? 'destructive' : 'secondary'
                      }>
                        {reportStatus.toUpperCase()}
                      </Badge>
                    </div>
                    {report?.currentAction && reportStatus === 'running' && (
                      <CardDescription className="flex items-center gap-2 mt-2 text-primary font-medium">
                        <span className="relative flex h-2 w-2">
                          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                          <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
                        </span>
                        {report.currentAction}
                      </CardDescription>
                    )}
                  </CardHeader>
                  <CardContent className="flex-1 p-0">
                    <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
                      {!report ? (
                        <p className="text-sm text-muted-foreground text-center py-8">
                          Waiting for agent to initialize...
                        </p>
                      ) : (
                        <>
                          {report.findings?.length > 0 ? (
                            <ul className="space-y-4">
                              {report.findings.map((finding, i) => {
                                const text = getFindingText(finding);
                                return (
                                  <li key={i} className="text-sm space-y-2">
                                    <div className="flex gap-3">
                                      <span className="text-primary mt-0.5 shrink-0">•</span>
                                      <span className="block">{text}</span>
                                    </div>
                                  </li>
                                );
                              })}
                            </ul>
                          ) : (
                            <p className="text-sm text-muted-foreground text-center py-8">
                              No findings recorded yet.
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        <TabsContent value="screenshots" className="mt-6 space-y-10">
          {screenshotGroups.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-16">
              No screenshots captured yet — they appear here as agents browse.
            </p>
          ) : (
            screenshotGroups.map(({ imgUrl, pageUrl, isMobile, agents }) => (
              <div key={imgUrl} className="rounded-lg border overflow-hidden shadow-sm flex flex-col md:flex-row bg-background">
                {/* Image Section - Constrained width on desktop, full width on mobile */}
                <div className={`border-b md:border-b-0 md:border-r bg-muted/10 p-6 flex items-start justify-center shrink-0 ${isMobile ? 'md:w-[350px]' : 'md:w-[600px]'}`}>
                  <a href={imgUrl} target="_blank" rel="noreferrer" className="block relative w-full group">
                    <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center rounded-md pointer-events-none z-10">
                      <span className="text-white text-sm font-medium bg-black/60 px-3 py-1.5 rounded-full backdrop-blur-sm">View full size</span>
                    </div>
                    <div className="relative inline-block w-full">
                      <img 
                        src={imgUrl} 
                        alt={`Agent view of ${pageUrl}`} 
                        className="w-full shadow-sm border object-contain object-top bg-white"
                        style={{ maxHeight: isMobile ? '800px' : '450px' }}
                      />
                    </div>
                  </a>
                </div>

                {/* Content Section - URL and Findings */}
                <div className="flex flex-col flex-1 min-w-0">
                  {/* URL bar */}
                  <div className="px-5 py-3 border-b bg-muted/30 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-muted-foreground"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></svg>
                      <p className="text-sm font-mono text-muted-foreground truncate" title={pageUrl}>{pageUrl || '—'}</p>
                    </div>
                    <Badge variant="secondary" className="bg-muted/50 text-[10px] uppercase h-5 shrink-0">
                      {isMobile ? 'Mobile' : 'Desktop'}
                    </Badge>
                  </div>

                  {/* Per-persona findings */}
                  <div className="divide-y flex-1 overflow-y-auto">
                    {agents.map(({ name, findings }) => (
                      <div key={name} className="p-5 space-y-3">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className="uppercase tracking-wide text-[10px] font-semibold">
                            {name}
                          </Badge>
                        </div>
                        {findings.length > 0 ? (
                          <ul className="space-y-3">
                            {findings.map((f, i) => {
                              const text = getFindingText(f);

                              return (
                                <li key={i} className="text-sm text-muted-foreground leading-relaxed bg-muted/20 p-3 rounded-md border">
                                  <div className="flex gap-3 items-start">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-primary mt-0.5"><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/><path d="m9 12 2 2 4-4"/></svg>
                                    <div>
                                      <span className="italic">{text}</span>
                                    </div>
                                  </div>
                                </li>
                              );
                            })}
                          </ul>
                        ) : (
                          <p className="text-sm text-muted-foreground/70 italic px-2">No specific findings logged for this page by {name}.</p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ))
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}