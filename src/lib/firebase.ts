import { initializeApp, getApps } from "firebase/app";
import { getAuth } from "firebase/auth";
import {
  initializeFirestore,
  getFirestore,
  enableNetwork,
  disableNetwork,
  memoryLocalCache,
} from "firebase/firestore";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  measurementId: process.env.NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID,
};

// Initialize Firebase only once
const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];
const auth = getAuth(app);

// Force long polling for Firestore listeners during audits. It is slower than
// the default transport, but it is much more stable on flaky local networks,
// VPNs, proxies, and environments where QUIC/WebChannel traffic drops.
const db = (() => {
  try {
    return initializeFirestore(app, {
      localCache: memoryLocalCache(),
      experimentalForceLongPolling: true,
    });
  } catch {
    return getFirestore(app);
  }
})();

async function reconnectFirestoreNetwork() {
  try {
    await disableNetwork(db);
  } catch {
    // Ignore; we just want to force a clean reconnect cycle.
  }
  await enableNetwork(db);
}

export { app, auth, db, reconnectFirestoreNetwork };