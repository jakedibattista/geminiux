import { NextResponse } from 'next/server';
import { adminAuth } from '@/lib/firebase-admin';

export async function POST(request: Request) {
  try {
    const { idToken } = await request.json();

    // Create a session cookie valid for 5 days
    const expiresIn = 60 * 60 * 24 * 5 * 1000;
    
    // Set session cookie
    const sessionCookie = await adminAuth.createSessionCookie(idToken, {
      expiresIn,
    });

    // Create the response
    const response = NextResponse.json({ success: true }, { status: 200 });

    // Set the cookie on the response
    response.cookies.set({
      name: '__session', // Must be __session for Firebase hosting compatibility if we ever use it
      value: sessionCookie,
      maxAge: expiresIn / 1000, // maxAge is in seconds for cookies
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      path: '/',
      sameSite: 'lax',
    });

    return response;
  } catch (error) {
    console.error('Session creation error:', error);
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
}

export async function DELETE() {
  const response = NextResponse.json({ success: true }, { status: 200 });
  
  response.cookies.set({
    name: '__session',
    value: '',
    maxAge: 0,
    path: '/',
  });

  return response;
}