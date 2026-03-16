import { NextResponse } from 'next/server';
import { adminDb, adminAuth } from '@/lib/firebase-admin';
import { v4 as uuidv4 } from 'uuid';
import { cookies } from 'next/headers';

export async function POST(request: Request) {
  try {
    // 1. Authenticate user from session cookie
    const cookieStore = await cookies();
    const sessionCookie = cookieStore.get('__session')?.value;
    
    if (!sessionCookie) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const decodedClaims = await adminAuth.verifySessionCookie(sessionCookie, true);
    const userId = decodedClaims.uid;

    // 2. Parse request body
    const body = await request.json();
    const { url, personaIds, loginUrl, loginEmail, loginPassword } = body;

    if (!url || !personaIds || !Array.isArray(personaIds)) {
      return NextResponse.json({ error: 'Invalid input' }, { status: 400 });
    }

    // 3. Create Audit Document in Firestore
    const auditId = uuidv4();
    const now = new Date().toISOString();
    
    // Save the full custom persona data so the frontend can display their names
    const customPersonas = body.customPersonas || [];

    await adminDb.collection('audits').doc(auditId).set({
      userId,
      url,
      selectedPersonaIds: personaIds,
      customPersonas, 
      status: 'pending', // pending -> running -> completed/error
      createdAt: now,
      updatedAt: now,
    });

    // 4. Trigger the Cloud Run agent backend.
    // Cloud Run returns immediately with {"status": "accepted"} and runs the audit
    // as a background task, so awaiting this adds only ~200ms of latency.
    // Previously this was fire-and-forget, but Vercel terminates serverless functions
    // as soon as a response is returned, which abandoned the fetch before it completed.
    const cloudRunUrl = process.env.AGENT_BACKEND_URL;
    const apiSecret = process.env.AGENT_API_SECRET;

    if (cloudRunUrl) {
      try {
        const triggerRes = await fetch(`${cloudRunUrl}/api/run_audit`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Api-Secret': apiSecret || '',
          },
          body: JSON.stringify({
            auditId,
            url,
            personaIds,
            userId,
            customPersonas,
            ...(loginUrl && loginEmail && loginPassword ? { loginUrl, loginEmail, loginPassword } : {}),
          }),
          signal: AbortSignal.timeout(10000),
        });
        if (!triggerRes.ok) {
          throw new Error(`Cloud Run responded with ${triggerRes.status}`);
        }
      } catch (err) {
        console.error('Failed to trigger Cloud Run agent backend:', err);
        await adminDb.collection('audits').doc(auditId).update({
          status: 'error',
          errorMsg: 'Failed to connect to agent backend',
          updatedAt: new Date().toISOString(),
        });
        return NextResponse.json({ error: 'Agent backend unavailable' }, { status: 502 });
      }
    } else {
      console.warn('AGENT_BACKEND_URL is not set. Agents will not be triggered.');
    }

    // 5. Return success once backend has accepted the job
    return NextResponse.json({ success: true, auditId });
  } catch (error) {
    console.error('Error starting audit:', error);
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}