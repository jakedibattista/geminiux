import * as admin from 'firebase-admin';

function getAdminApp(): admin.app.App {
  if (admin.apps.length > 0) {
    return admin.app();
  }

  const raw = process.env.FIREBASE_SERVICE_ACCOUNT;
  if (!raw) {
    throw new Error('FIREBASE_SERVICE_ACCOUNT environment variable is not set');
  }

  return admin.initializeApp({
    credential: admin.credential.cert(JSON.parse(raw)),
  });
}

// Lazy proxy — getAdminApp() is only called when a property is first accessed,
// which happens inside a request handler, not at module load / build time.
export const adminAuth: admin.auth.Auth = new Proxy({} as admin.auth.Auth, {
  get(_, prop) {
    const auth = getAdminApp().auth();
    const val = (auth as any)[prop as string];
    return typeof val === 'function' ? val.bind(auth) : val;
  },
});

export const adminDb: admin.firestore.Firestore = new Proxy(
  {} as admin.firestore.Firestore,
  {
    get(_, prop) {
      const db = getAdminApp().firestore();
      const val = (db as any)[prop as string];
      return typeof val === 'function' ? val.bind(db) : val;
    },
  }
);
