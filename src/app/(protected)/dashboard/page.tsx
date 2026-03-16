'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { collection, query, where, getDocs, deleteDoc, doc } from 'firebase/firestore';
import { db, auth } from '@/lib/firebase';
import { onAuthStateChanged } from 'firebase/auth';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Trash2 } from 'lucide-react';

type Audit = {
  id: string;
  url: string;
  status: string;
  createdAt: string;
};

export default function DashboardPage() {
  const [audits, setAudits] = useState<Audit[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (user) => {
      if (user) {
        try {
          const q = query(
            collection(db, 'audits'),
            where('userId', '==', user.uid),
            // Note: ordering requires a composite index in Firestore if combined with where clause
            // orderBy('createdAt', 'desc') 
          );
          
          const querySnapshot = await getDocs(q);
          const auditsData = querySnapshot.docs.map(doc => ({
            id: doc.id,
            ...doc.data()
          })) as Audit[];
          
          // Sort manually on client to avoid needing to create an index right away during dev
          auditsData.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
          
          setAudits(auditsData);
        } catch (error) {
          console.error("Error fetching audits:", error);
        } finally {
          setLoading(false);
        }
      } else {
        setLoading(false);
      }
    });

    return () => unsubscribe();
  }, []);

  const handleLogout = async () => {
    await fetch('/api/session', { method: 'DELETE' });
    await auth.signOut();
    window.location.href = '/';
  };

  const handleDelete = async (e: React.MouseEvent, auditId: string) => {
    e.preventDefault(); // Prevent triggering the Link click
    if (!confirm('Are you sure you want to delete this audit?')) return;
    try {
      await deleteDoc(doc(db, 'audits', auditId));
      setAudits(audits.filter(a => a.id !== auditId));
    } catch (error) {
      console.error("Error deleting audit:", error);
      alert("Failed to delete audit.");
    }
  };

  return (
    <div className="container mx-auto p-8 space-y-8">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-4xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground mt-2">Manage your UX audits</p>
        </div>
        <div className="flex gap-4">
          <Link href="/audit/new">
            <Button>New Audit</Button>
          </Link>
          <Button variant="outline" onClick={handleLogout}>Log out</Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {loading ? (
          <p>Loading audits...</p>
        ) : audits.length === 0 ? (
          <Card className="col-span-full py-8 text-center bg-muted/20">
            <CardContent>
              <p className="text-muted-foreground mb-4">You haven&apos;t run any audits yet.</p>
              <Link href="/audit/new">
                <Button>Start your first audit</Button>
              </Link>
            </CardContent>
          </Card>
        ) : (
          audits.map((audit) => (
            <Link href={`/audit/${audit.id}`} key={audit.id}>
              <Card className="hover:border-primary transition-colors cursor-pointer h-full">
                <CardHeader>
                  <CardTitle className="truncate" title={audit.url}>{audit.url}</CardTitle>
                  <CardDescription>
                    {new Date(audit.createdAt).toLocaleDateString()}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex justify-between items-center">
                    <span className={`px-2 py-1 rounded text-xs font-semibold ${
                      audit.status === 'completed' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400' :
                      audit.status === 'running' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400' :
                      audit.status === 'error' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400' :
                      'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300'
                    }`}>
                      {audit.status.toUpperCase()}
                    </span>
                    <Button 
                      variant="ghost" 
                      size="icon" 
                      onClick={(e) => handleDelete(e, audit.id)}
                      className="h-8 w-8 text-muted-foreground hover:text-destructive"
                      title="Delete audit"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))
        )}
      </div>
    </div>
  );
}