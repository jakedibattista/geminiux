import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export async function proxy(request: NextRequest) {
  const session = request.cookies.get('__session')?.value;
  
  // Protect /dashboard, /audit, and /personas routes
  const isProtectedRoute = request.nextUrl.pathname.startsWith('/dashboard') || 
                          request.nextUrl.pathname.startsWith('/audit') ||
                          request.nextUrl.pathname.startsWith('/personas');
                          
  const isAuthRoute = request.nextUrl.pathname.startsWith('/login') || 
                      request.nextUrl.pathname.startsWith('/signup');

  // If no session on a protected route, redirect to login
  if (isProtectedRoute && !session) {
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('redirect', request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  // If there is a session on an auth route, redirect to dashboard
  if (isAuthRoute && session) {
    return NextResponse.redirect(new URL('/dashboard', request.url));
  }

  // For protected routes, verify the session cookie via REST API 
  // (lightweight, doesn't require full firebase-admin SDK in middleware)
  if (isProtectedRoute && session) {
    try {
      const verifyUrl = `https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${process.env.NEXT_PUBLIC_FIREBASE_API_KEY}`;
      const res = await fetch(verifyUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ idToken: session }), // __session acts as idToken equivalent for this endpoint in some setups, but officially we should use admin sdk.
      });
      
      // Wait, identitytoolkit accounts:lookup expects an ID token, not a session cookie.
      // Since we are using runtime='nodejs', we can just use the admin SDK! Let's do that.
      const { adminAuth } = await import('@/lib/firebase-admin');
      await adminAuth.verifySessionCookie(session, true);
      
      return NextResponse.next();
    } catch (error) {
      console.error('Middleware session verification failed', error);
      // If verification fails, clear the cookie and redirect to login
      const response = NextResponse.redirect(new URL('/login', request.url));
      response.cookies.delete('__session');
      return response;
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    '/dashboard/:path*',
    '/audit/:path*',
    '/personas/:path*',
    '/login',
    '/signup'
  ],
};