'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Persona } from '@/components/audit/PersonaSelector';
import { Sparkles, Monitor, Smartphone } from 'lucide-react';

export function PersonaBuilder({ onComplete }: { onComplete: (persona: Omit<Persona, 'id'>) => void }) {
  const [description, setDescription] = useState('');
  const [deviceType, setDeviceType] = useState<'desktop' | 'mobile'>('desktop');
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    if (!description.trim()) return;
    
    setIsGenerating(true);
    setError(null);
    
    try {
      const res = await fetch('/api/personas/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description, deviceType }),
      });
      
      if (!res.ok) throw new Error('Failed to generate persona');
      
      const data = await res.json();
      onComplete(data.persona);
      setDescription('');
      setDeviceType('desktop');
    } catch (err: any) {
      console.error(err);
      setError(err.message || 'Error communicating with AI.');
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-primary" />
          AI Persona Builder
        </CardTitle>
        <CardDescription>
          Describe your target user in plain English, and Gemini will generate a structured testing persona for you.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="description">User Description</Label>
          <Textarea
            id="description"
            placeholder="e.g. An elderly person who isn't great with technology, uses an iPad, and needs high contrast text because of poor eyesight."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="min-h-[100px]"
          />
        </div>

        <div className="space-y-2">
          <Label>Device</Label>
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => setDeviceType('desktop')}
              className={`flex items-center justify-center gap-2 rounded-lg border-2 px-4 py-3 text-sm font-medium transition-all ${
                deviceType === 'desktop'
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-border text-muted-foreground hover:border-muted-foreground'
              }`}
            >
              <Monitor className="w-4 h-4" />
              Desktop
            </button>
            <button
              type="button"
              onClick={() => setDeviceType('mobile')}
              className={`flex items-center justify-center gap-2 rounded-lg border-2 px-4 py-3 text-sm font-medium transition-all ${
                deviceType === 'mobile'
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-border text-muted-foreground hover:border-muted-foreground'
              }`}
            >
              <Smartphone className="w-4 h-4" />
              Mobile
            </button>
          </div>
        </div>

        {error && <p className="text-sm text-red-500">{error}</p>}
      </CardContent>
      <CardFooter>
        <Button 
          onClick={handleGenerate} 
          disabled={isGenerating || !description.trim()}
          className="w-full"
        >
          {isGenerating ? 'Generating Persona...' : 'Generate Persona'}
        </Button>
      </CardFooter>
    </Card>
  );
}