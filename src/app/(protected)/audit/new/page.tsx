'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { collection, onSnapshot, doc, setDoc, deleteDoc } from 'firebase/firestore';
import { auth, db } from '@/lib/firebase';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { PersonaSelector, defaultPersonas, Persona } from '@/components/audit/PersonaSelector';
import { PersonaBuilder } from '@/components/audit/PersonaBuilder';
import { PersonaEditorDialog } from '@/components/audit/PersonaEditorDialog';

export default function NewAuditPage() {
  const [url, setUrl] = useState('');
  const [customPersonas, setCustomPersonas] = useState<Persona[]>([]);
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>(
    defaultPersonas
      .filter(persona => ['p_first_time', 'p_mobile', 'p_accessibility'].includes(persona.id))
      .map(persona => persona.id)
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSavingPersona, setIsSavingPersona] = useState(false);
  const [showAuth, setShowAuth] = useState(false);
  const [loginUrl, setLoginUrl] = useState('');
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [editingPersona, setEditingPersona] = useState<Persona | null>(null);
  const router = useRouter();

  // Load saved custom personas from Firestore and keep them in sync
  useEffect(() => {
    const uid = auth.currentUser?.uid;
    if (!uid) return;

    const ref = collection(db, 'users', uid, 'customPersonas');
    const unsubscribe = onSnapshot(ref, snapshot => {
      const loaded = snapshot.docs.map(d => ({ id: d.id, ...d.data() } as Persona));
      setCustomPersonas(loaded);
    });
    return unsubscribe;
  }, []);

  const handleNewPersona = async (newPersonaData: Omit<Persona, 'id'>) => {
    const uid = auth.currentUser?.uid;
    const id = `p_custom_${Date.now()}`;
    const newPersona: Persona = { ...newPersonaData, id };

    if (uid) {
      // Save to Firestore — onSnapshot will update customPersonas automatically
      await setDoc(doc(db, 'users', uid, 'customPersonas', id), newPersonaData);
    } else {
      // Fallback: local state only (shouldn't happen in normal use)
      setCustomPersonas(prev => [...prev, newPersona]);
    }
    setSelectedPersonas(prev => [...prev, id]);
  };

  const handleDeletePersona = async (id: string) => {
    const uid = auth.currentUser?.uid;
    if (uid) {
      await deleteDoc(doc(db, 'users', uid, 'customPersonas', id));
    } else {
      setCustomPersonas(prev => prev.filter(p => p.id !== id));
    }
    setSelectedPersonas(prev => prev.filter(pid => pid !== id));
  };

  const handleSavePersonaEdits = async (updates: Omit<Persona, 'id'>) => {
    if (!editingPersona) return;

    const updatedPersona: Persona = { ...editingPersona, ...updates };
    const uid = auth.currentUser?.uid;
    setIsSavingPersona(true);

    try {
      if (uid) {
        await setDoc(doc(db, 'users', uid, 'customPersonas', editingPersona.id), updates);
      } else {
        setCustomPersonas(prev => prev.map(persona => (
          persona.id === editingPersona.id ? updatedPersona : persona
        )));
      }
      setEditingPersona(null);
    } catch (error) {
      console.error(error);
      alert('Failed to save persona changes.');
    } finally {
      setIsSavingPersona(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!url) return alert("Please enter a URL");
    if (selectedPersonas.length === 0) return alert("Please select at least one persona");

    setIsSubmitting(true);

    try {
      const res = await fetch('/api/audit/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          url,
          personaIds: selectedPersonas,
          customPersonas,
          ...(showAuth && loginUrl && loginEmail && loginPassword ? {
            loginUrl,
            loginEmail,
            loginPassword,
          } : {}),
        }),
      });

      if (!res.ok) throw new Error("Failed to start audit");

      const data = await res.json();
      router.push(`/audit/${data.auditId}`);
    } catch (error) {
      console.error(error);
      alert("Something went wrong starting the audit.");
      setIsSubmitting(false);
    }
  };

  return (
    <div className="container mx-auto p-8 max-w-5xl space-y-8">
      <div>
        <h1 className="text-4xl font-bold">New UX Audit</h1>
        <p className="text-muted-foreground mt-2">Configure the AI agents that will test your site.</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-8">
        <Card>
          <CardHeader>
            <CardTitle>1. Target URL</CardTitle>
            <CardDescription>The website you want the agents to analyze</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <Label htmlFor="url">Website URL</Label>
              <Input
                id="url"
                type="url"
                placeholder="https://yourwebsite.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>2. Authentication <span className="text-muted-foreground font-normal text-sm">(optional)</span></CardTitle>
                <CardDescription>Required only if the target URL is behind a login wall</CardDescription>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowAuth(v => !v)}
              >
                {showAuth ? 'Remove' : 'Add login credentials'}
              </Button>
            </div>
          </CardHeader>
          {showAuth && (
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="loginUrl">Login page URL</Label>
                <Input
                  id="loginUrl"
                  type="url"
                  placeholder="https://yourwebsite.com/login"
                  value={loginUrl}
                  onChange={e => setLoginUrl(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="loginEmail">Email / Username</Label>
                  <Input
                    id="loginEmail"
                    type="text"
                    placeholder="test@example.com"
                    value={loginEmail}
                    onChange={e => setLoginEmail(e.target.value)}
                    autoComplete="off"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="loginPassword">Password</Label>
                  <Input
                    id="loginPassword"
                    type="password"
                    placeholder="••••••••"
                    value={loginPassword}
                    onChange={e => setLoginPassword(e.target.value)}
                    autoComplete="new-password"
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Credentials are sent directly to the agent backend and never stored in the database.
              </p>
            </CardContent>
          )}
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>3. Select Personas</CardTitle>
            <CardDescription>
              Choose the simulated user perspectives for this audit. Each persona runs as a parallel AI agent.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-8">
            <PersonaSelector 
              selectedIds={selectedPersonas} 
              onChange={setSelectedPersonas} 
              customPersonas={customPersonas}
              onDeleteCustom={handleDeletePersona}
              onEditCustom={setEditingPersona}
            />
            
            <div className="pt-4 border-t">
              <h3 className="text-lg font-semibold mb-4">Need a specific user type?</h3>
              <PersonaBuilder onComplete={handleNewPersona} />
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-end gap-4">
          <Button 
            variant="outline" 
            type="button" 
            onClick={() => router.back()}
            disabled={isSubmitting}
          >
            Cancel
          </Button>
          <Button 
            type="submit" 
            size="lg"
            disabled={isSubmitting || selectedPersonas.length === 0 || !url}
          >
            {isSubmitting ? 'Launching Agents...' : 'Run Audit'}
          </Button>
        </div>
      </form>

      <PersonaEditorDialog
        persona={editingPersona}
        open={Boolean(editingPersona)}
        onOpenChange={(open) => {
          if (!open) setEditingPersona(null);
        }}
        onSave={handleSavePersonaEdits}
        isSaving={isSavingPersona}
      />
    </div>
  );
}