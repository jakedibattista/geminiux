'use client';

import { useMemo, useState } from 'react';
import { Persona } from '@/components/audit/PersonaSelector';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type EditablePersona = Pick<Persona, 'name' | 'description' | 'goals' | 'deviceType'>;

type Props = {
  persona: Persona | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (updates: EditablePersona) => Promise<void>;
  isSaving?: boolean;
};

export function PersonaEditorDialog({
  persona,
  open,
  onOpenChange,
  onSave,
  isSaving = false,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit Custom Persona</DialogTitle>
          <DialogDescription>
            Fine-tune the generated persona before using it in future audits.
          </DialogDescription>
        </DialogHeader>

        {persona ? (
          <PersonaEditorForm
            key={persona.id}
            persona={persona}
            onOpenChange={onOpenChange}
            onSave={onSave}
            isSaving={isSaving}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function PersonaEditorForm({
  persona,
  onOpenChange,
  onSave,
  isSaving,
}: {
  persona: Persona;
  onOpenChange: (open: boolean) => void;
  onSave: (updates: EditablePersona) => Promise<void>;
  isSaving: boolean;
}) {
  const [name, setName] = useState(persona.name);
  const [description, setDescription] = useState(persona.description);
  const [goalsText, setGoalsText] = useState(persona.goals.join('\n'));
  const [deviceType, setDeviceType] = useState<'desktop' | 'mobile'>(persona.deviceType);
  const [error, setError] = useState<string | null>(null);

  const parsedGoals = useMemo(
    () => goalsText.split('\n').map(goal => goal.trim()).filter(Boolean),
    [goalsText]
  );

  const handleSubmit = async () => {
    const trimmedName = name.trim();
    const trimmedDescription = description.trim();

    if (!trimmedName) {
      setError('Name is required.');
      return;
    }
    if (!trimmedDescription) {
      setError('Description is required.');
      return;
    }
    if (parsedGoals.length === 0) {
      setError('Add at least one goal.');
      return;
    }

    setError(null);
    await onSave({
      name: trimmedName,
      description: trimmedDescription,
      goals: parsedGoals,
      deviceType,
    });
  };

  return (
    <>
      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="persona-name">Name</Label>
          <Input
            id="persona-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Persona name"
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="persona-description">Description</Label>
          <Textarea
            id="persona-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="min-h-[96px]"
            placeholder="Describe this user type and how they approach the site."
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="persona-goals">Goals</Label>
          <Textarea
            id="persona-goals"
            value={goalsText}
            onChange={(e) => setGoalsText(e.target.value)}
            className="min-h-[120px]"
            placeholder={'Enter one goal per line\nExample: Understand the value quickly'}
          />
          <p className="text-xs text-muted-foreground">
            Use one line per goal. These goals guide how the agent evaluates the experience.
          </p>
        </div>

        <div className="space-y-2">
          <Label>Device</Label>
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => setDeviceType('desktop')}
              className={`rounded-lg border-2 px-4 py-3 text-sm font-medium transition-all ${
                deviceType === 'desktop'
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-border text-muted-foreground hover:border-muted-foreground'
              }`}
            >
              Desktop
            </button>
            <button
              type="button"
              onClick={() => setDeviceType('mobile')}
              className={`rounded-lg border-2 px-4 py-3 text-sm font-medium transition-all ${
                deviceType === 'mobile'
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-border text-muted-foreground hover:border-muted-foreground'
              }`}
            >
              Mobile
            </button>
          </div>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={isSaving}>
          Cancel
        </Button>
        <Button type="button" onClick={handleSubmit} disabled={isSaving}>
          {isSaving ? 'Saving...' : 'Save Changes'}
        </Button>
      </DialogFooter>
    </>
  );
}
