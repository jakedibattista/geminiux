import { NextResponse } from 'next/server';
import { adminAuth } from '@/lib/firebase-admin';
import { VertexAI } from '@google-cloud/vertexai';
import { cookies } from 'next/headers';

export async function POST(request: Request) {
  try {
    const cookieStore = await cookies();
    const sessionCookie = cookieStore.get('__session')?.value;
    
    if (!sessionCookie) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    await adminAuth.verifySessionCookie(sessionCookie, true);

    const { description, deviceType } = await request.json();

    if (!description) {
      return NextResponse.json({ error: 'Description required' }, { status: 400 });
    }

    const resolvedDevice: 'mobile' | 'desktop' =
      deviceType === 'mobile' ? 'mobile' : 'desktop';

    const prompt = `
      You are an expert UX researcher. Based on the following brief description of a user, 
      generate a structured UX testing persona.
      
      User Description: "${description}"
      Device: ${resolvedDevice}
      
      Return ONLY a valid JSON object matching this exact structure:
      {
        "name": "A catchy, descriptive name for the persona (e.g. 'Frustrated Mobile Shopper')",
        "description": "A 1-2 sentence expansion on the provided description",
        "goals": ["Goal 1", "Goal 2", "Goal 3"],
        "deviceType": "${resolvedDevice}"
      }
      
      The deviceType field MUST be exactly "${resolvedDevice}" — do not change it.
    `;

    // Initialize the Vertex AI service using the admin credentials
    let credentials;
    if (process.env.FIREBASE_SERVICE_ACCOUNT) {
      credentials = JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT);
    }
    
    const vertexAI = new VertexAI({
      project: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || 'auditmysite-61bd1',
      location: 'us-central1',
      googleAuthOptions: credentials ? { credentials } : undefined
    });
    
    const model = vertexAI.getGenerativeModel({
      model: 'gemini-2.5-flash',
      generationConfig: {
        responseMimeType: 'application/json',
      }
    });

    const response = await model.generateContent(prompt);
    const text = response.response.candidates?.[0]?.content?.parts?.[0]?.text;
    
    if (!text) throw new Error("No text generated");

    const persona = JSON.parse(text);
    // Always enforce the user's explicit device choice regardless of what the model returned
    persona.deviceType = resolvedDevice;

    return NextResponse.json({ persona });
  } catch (error) {
    console.error('Error generating persona:', error);
    return NextResponse.json({ error: 'Failed to generate persona' }, { status: 500 });
  }
}